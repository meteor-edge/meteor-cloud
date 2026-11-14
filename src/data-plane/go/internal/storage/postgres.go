package storage

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"meteorcloud/dataplane/internal/envelope"
)

// migrations run in order, once each, inside the "dataplane" schema. The data plane
// owns this schema; it never reads control-plane tables.
var migrations = []string{
	`CREATE TABLE dataplane.events (
		organization_id text        NOT NULL,
		event_id        text        NOT NULL,
		stream          text        NOT NULL,
		kind            text        NOT NULL,
		type            text        NOT NULL,
		application_id  text        NOT NULL DEFAULT '',
		application     text        NOT NULL DEFAULT '',
		device_id       text        NOT NULL DEFAULT '',
		environment     text        NOT NULL DEFAULT '',
		ts              timestamptz NOT NULL,
		received_at     timestamptz NOT NULL,
		seq             bigint,
		schema_version  integer     NOT NULL,
		data            jsonb       NOT NULL,
		PRIMARY KEY (organization_id, event_id)
	);
	CREATE INDEX events_stream_ts ON dataplane.events (organization_id, stream, ts, event_id);
	CREATE INDEX events_ts ON dataplane.events (ts);

	CREATE TABLE dataplane.dead_letters (
		id              bigserial   PRIMARY KEY,
		received_at     timestamptz NOT NULL,
		source          text        NOT NULL,
		organization_id text        NOT NULL DEFAULT '',
		reason          text        NOT NULL,
		detail          text        NOT NULL DEFAULT '',
		payload         bytea
	);

	CREATE TABLE dataplane.commands (
		id              text        PRIMARY KEY,
		organization_id text        NOT NULL,
		application_id  text        NOT NULL,
		application     text        NOT NULL,
		device_id       text        NOT NULL DEFAULT '',
		name            text        NOT NULL,
		payload         jsonb       NOT NULL,
		state           text        NOT NULL,
		issued_by       text        NOT NULL,
		idempotency_key text,
		attempts        integer     NOT NULL DEFAULT 0,
		max_attempts    integer     NOT NULL,
		timeout_seconds integer     NOT NULL,
		created_at      timestamptz NOT NULL,
		updated_at      timestamptz NOT NULL,
		expires_at      timestamptz NOT NULL,
		dispatched_at   timestamptz,
		received_at     timestamptz,
		completed_at    timestamptz,
		result          jsonb,
		error           text        NOT NULL DEFAULT '',
		next_check_at   timestamptz NOT NULL
	);
	CREATE UNIQUE INDEX commands_idempotency ON dataplane.commands (organization_id, idempotency_key)
		WHERE idempotency_key IS NOT NULL;
	CREATE INDEX commands_due ON dataplane.commands (next_check_at)
		WHERE state IN ('PENDING','DISPATCHED','RECEIVED','EXECUTING');
	CREATE INDEX commands_target ON dataplane.commands (organization_id, application_id)
		WHERE state IN ('PENDING','DISPATCHED');`,
	// An idempotency key belongs to its sender: two senders in one organization may
	// use the same key without seeing or suppressing each other's commands.
	`DROP INDEX dataplane.commands_idempotency;
	CREATE UNIQUE INDEX commands_idempotency ON dataplane.commands (organization_id, issued_by, idempotency_key)
		WHERE idempotency_key IS NOT NULL;`,
}

// Postgres is the durable Store.
type Postgres struct {
	pool         *pgxpool.Pool
	queryTimeout time.Duration
}

// OpenPostgres connects, applies pending migrations and returns the store.
// queryTimeout bounds every historical query (statement_timeout).
func OpenPostgres(ctx context.Context, url string, maxConns int32, queryTimeout time.Duration) (*Postgres, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("parse database url: %w", err)
	}
	if maxConns > 0 {
		cfg.MaxConns = maxConns
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	p := &Postgres{pool: pool, queryTimeout: queryTimeout}
	if err := p.migrate(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return p, nil
}

func (p *Postgres) migrate(ctx context.Context) error {
	conn, err := p.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	// One replica migrates at a time.
	if _, err := conn.Exec(ctx, `SELECT pg_advisory_lock(778899001)`); err != nil {
		return err
	}
	defer conn.Exec(context.Background(), `SELECT pg_advisory_unlock(778899001)`)
	if _, err := conn.Exec(ctx, `CREATE SCHEMA IF NOT EXISTS dataplane;
		CREATE TABLE IF NOT EXISTS dataplane.schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return fmt.Errorf("prepare migrations: %w", err)
	}
	var current int
	if err := conn.QueryRow(ctx, `SELECT COALESCE(MAX(version), 0) FROM dataplane.schema_migrations`).Scan(&current); err != nil {
		return err
	}
	for i := current; i < len(migrations); i++ {
		tx, err := conn.Begin(ctx)
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, migrations[i]); err != nil {
			tx.Rollback(ctx)
			return fmt.Errorf("migration %d: %w", i+1, err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO dataplane.schema_migrations (version) VALUES ($1)`, i+1); err != nil {
			tx.Rollback(ctx)
			return err
		}
		if err := tx.Commit(ctx); err != nil {
			return err
		}
	}
	return nil
}

func (p *Postgres) Ping(ctx context.Context) error { return p.pool.Ping(ctx) }
func (p *Postgres) Close()                         { p.pool.Close() }

func (p *Postgres) InsertEvents(ctx context.Context, events []*envelope.Event) ([]bool, error) {
	n := len(events)
	orgs, ids, strms, kinds, types := make([]string, n), make([]string, n), make([]string, n), make([]string, n), make([]string, n)
	appIDs, apps, devs, envs := make([]string, n), make([]string, n), make([]string, n), make([]string, n)
	tss, recvs := make([]time.Time, n), make([]time.Time, n)
	seqs := make([]*int64, n)
	vers := make([]int32, n)
	datas := make([]string, n)
	for i, ev := range events {
		orgs[i], ids[i], strms[i], kinds[i], types[i] = ev.OrganizationID, ev.ID, ev.Stream, ev.Kind, ev.Type
		appIDs[i], apps[i], devs[i], envs[i] = ev.ApplicationID, ev.Application, ev.DeviceID, ev.Environment
		tss[i], recvs[i], seqs[i], vers[i], datas[i] = ev.Time, ev.ReceivedAt, ev.Seq, int32(ev.SchemaVersion), string(ev.Data)
	}
	rows, err := p.pool.Query(ctx, `
		INSERT INTO dataplane.events
			(organization_id, event_id, stream, kind, type, application_id, application, device_id, environment,
			 ts, received_at, seq, schema_version, data)
		SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[], $7::text[],
			$8::text[], $9::text[], $10::timestamptz[], $11::timestamptz[], $12::bigint[], $13::int[], $14::jsonb[])
		ON CONFLICT (organization_id, event_id) DO NOTHING
		RETURNING organization_id, event_id`,
		orgs, ids, strms, kinds, types, appIDs, apps, devs, envs, tss, recvs, seqs, vers, datas)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	stored := make(map[string]struct{}, n)
	for rows.Next() {
		var org, id string
		if err := rows.Scan(&org, &id); err != nil {
			return nil, err
		}
		stored[org+"\x00"+id] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	inserted := make([]bool, n)
	for i, ev := range events {
		key := ev.OrganizationID + "\x00" + ev.ID
		if _, ok := stored[key]; ok {
			inserted[i] = true
			delete(stored, key) // a duplicate inside the same batch counts once
		}
	}
	return inserted, nil
}

func (q *Query) where(args *[]any) string {
	add := func(v any) string {
		*args = append(*args, v)
		return fmt.Sprintf("$%d", len(*args))
	}
	conds := []string{"organization_id = " + add(q.OrganizationID), "stream = " + add(q.Stream)}
	if q.Kind != "" {
		conds = append(conds, "kind = "+add(q.Kind))
	}
	if q.DeviceID != "" {
		conds = append(conds, "device_id = "+add(q.DeviceID))
	}
	if q.Application != "" {
		conds = append(conds, "application = "+add(q.Application))
	}
	if !q.Start.IsZero() {
		conds = append(conds, "ts >= "+add(q.Start))
	}
	if !q.End.IsZero() {
		conds = append(conds, "ts < "+add(q.End))
	}
	if len(q.Filter) > 0 {
		raw, _ := json.Marshal(q.Filter)
		conds = append(conds, "data @> "+add(string(raw))+"::jsonb")
	}
	return strings.Join(conds, " AND ")
}

// bounded runs fn in a read-only transaction with a statement timeout so one
// expensive query cannot exhaust the database.
func (p *Postgres) bounded(ctx context.Context, fn func(tx pgx.Tx) error) error {
	tx, err := p.pool.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if p.queryTimeout > 0 {
		if _, err := tx.Exec(ctx, fmt.Sprintf("SET LOCAL statement_timeout = %d", p.queryTimeout.Milliseconds())); err != nil {
			return err
		}
	}
	return fn(tx)
}

func (p *Postgres) QueryEvents(ctx context.Context, q Query) ([]*envelope.Event, *Cursor, error) {
	var args []any
	where := q.where(&args)
	order, cmp := "ASC", ">"
	if q.Descending {
		order, cmp = "DESC", "<"
	}
	if q.After != nil {
		args = append(args, q.After.Time, q.After.ID)
		where += fmt.Sprintf(" AND (ts, event_id) %s ($%d, $%d)", cmp, len(args)-1, len(args))
	}
	args = append(args, q.Limit+1)
	sql := fmt.Sprintf(`SELECT event_id, stream, kind, type, organization_id, application_id, application, device_id,
		environment, ts, received_at, seq, schema_version, data
		FROM dataplane.events WHERE %s ORDER BY ts %s, event_id %s LIMIT $%d`, where, order, order, len(args))
	var out []*envelope.Event
	err := p.bounded(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, sql, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			ev := &envelope.Event{}
			var data []byte
			if err := rows.Scan(&ev.ID, &ev.Stream, &ev.Kind, &ev.Type, &ev.OrganizationID, &ev.ApplicationID,
				&ev.Application, &ev.DeviceID, &ev.Environment, &ev.Time, &ev.ReceivedAt, &ev.Seq,
				&ev.SchemaVersion, &data); err != nil {
				return err
			}
			ev.Time, ev.ReceivedAt, ev.Data = ev.Time.UTC(), ev.ReceivedAt.UTC(), data
			out = append(out, ev)
		}
		return rows.Err()
	})
	if err != nil {
		return nil, nil, err
	}
	var next *Cursor
	if len(out) > q.Limit {
		out = out[:q.Limit]
		last := out[len(out)-1]
		next = &Cursor{Time: last.Time, ID: last.ID}
	}
	return out, next, nil
}

var aggregateSQL = map[string]string{"avg": "avg(v)", "min": "min(v)", "max": "max(v)", "sum": "sum(v)", "count": "count(*)::float8"}

func (p *Postgres) Aggregate(ctx context.Context, q AggregateQuery) ([]Bucket, error) {
	expr, ok := aggregateSQL[q.Func]
	if !ok {
		return nil, fmt.Errorf("unsupported aggregate %q", q.Func)
	}
	var args []any
	where := q.Query.where(&args)
	args = append(args, q.Field, fmt.Sprintf("%d microseconds", q.Bucket.Microseconds()))
	pathArg, bucketArg := len(args)-1, len(args)
	// Non-numeric values, and numbers too large for a float, are ignored instead of
	// failing the query.
	sql := fmt.Sprintf(`
		SELECT bucket, %s, count(v) FROM (
			SELECT date_bin($%d::interval, ts, TIMESTAMPTZ 'epoch') AS bucket,
				CASE WHEN jsonb_typeof(data #> $%d::text[]) = 'number'
					AND abs((data #> $%d::text[])::numeric) < 1e308
					THEN (data #>> $%d::text[])::float8 END AS v
			FROM dataplane.events WHERE %s
		) s GROUP BY bucket ORDER BY bucket`, expr, bucketArg, pathArg, pathArg, pathArg, where)
	var out []Bucket
	err := p.bounded(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, sql, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var b Bucket
			if err := rows.Scan(&b.Start, &b.Value, &b.Count); err != nil {
				return err
			}
			b.Start = b.Start.UTC()
			if q.Func == "count" && b.Value != nil {
				b.Count = int64(*b.Value)
			}
			out = append(out, b)
		}
		return rows.Err()
	})
	return out, err
}

func (p *Postgres) DeleteEventsBefore(ctx context.Context, before time.Time) (int64, error) {
	tag, err := p.pool.Exec(ctx, `DELETE FROM dataplane.events WHERE ts < $1`, before)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

func (p *Postgres) AddDeadLetter(ctx context.Context, d DeadLetter) error {
	_, err := p.pool.Exec(ctx, `INSERT INTO dataplane.dead_letters (received_at, source, organization_id, reason, detail, payload)
		VALUES ($1, $2, $3, $4, $5, $6)`, d.ReceivedAt, d.Source, d.OrganizationID, d.Reason, d.Detail, d.Payload)
	return err
}

const commandColumns = `id, organization_id, application_id, application, device_id, name, payload, state, issued_by,
	COALESCE(idempotency_key, ''), attempts, max_attempts, timeout_seconds, created_at, updated_at, expires_at,
	dispatched_at, received_at, completed_at, result, error, next_check_at`

func scanCommand(row pgx.Row) (*Command, error) {
	c := &Command{}
	var payload, result []byte
	err := row.Scan(&c.ID, &c.OrganizationID, &c.ApplicationID, &c.Application, &c.DeviceID, &c.Name, &payload,
		&c.State, &c.IssuedBy, &c.IdempotencyKey, &c.Attempts, &c.MaxAttempts, &c.TimeoutSeconds, &c.CreatedAt,
		&c.UpdatedAt, &c.ExpiresAt, &c.DispatchedAt, &c.ReceivedAt, &c.CompletedAt, &result, &c.Error, &c.NextCheckAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	c.Payload, c.Result = payload, result
	return c, nil
}

func (p *Postgres) CreateCommand(ctx context.Context, c *Command) (*Command, bool, error) {
	var key *string
	if c.IdempotencyKey != "" {
		key = &c.IdempotencyKey
	}
	row := p.pool.QueryRow(ctx, `INSERT INTO dataplane.commands
		(id, organization_id, application_id, application, device_id, name, payload, state, issued_by, idempotency_key,
		 attempts, max_attempts, timeout_seconds, created_at, updated_at, expires_at, next_check_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,0,$11,$12,$13,$13,$14,$15)
		ON CONFLICT (organization_id, issued_by, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
		RETURNING `+commandColumns,
		c.ID, c.OrganizationID, c.ApplicationID, c.Application, c.DeviceID, c.Name, string(c.Payload), c.State,
		c.IssuedBy, key, c.MaxAttempts, c.TimeoutSeconds, c.CreatedAt, c.ExpiresAt, c.NextCheckAt)
	created, err := scanCommand(row)
	if err == nil {
		return created, true, nil
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, false, err
	}
	existing, err := scanCommand(p.pool.QueryRow(ctx, `SELECT `+commandColumns+` FROM dataplane.commands
		WHERE organization_id = $1 AND issued_by = $2 AND idempotency_key = $3`, c.OrganizationID, c.IssuedBy, c.IdempotencyKey))
	return existing, false, err
}

func (p *Postgres) GetCommand(ctx context.Context, organizationID, id string) (*Command, error) {
	return scanCommand(p.pool.QueryRow(ctx, `SELECT `+commandColumns+` FROM dataplane.commands
		WHERE organization_id = $1 AND id = $2`, organizationID, id))
}

func (p *Postgres) UpdateCommand(ctx context.Context, organizationID, id string, u CommandUpdate) (*Command, bool, error) {
	var result *string
	if u.Result != nil {
		s := string(u.Result)
		result = &s
	}
	var state *string
	if u.State != "" {
		state = &u.State
	}
	row := p.pool.QueryRow(ctx, `UPDATE dataplane.commands SET
			state = COALESCE($4, state),
			attempts = attempts + $5,
			dispatched_at = COALESCE($6, dispatched_at),
			received_at = COALESCE(received_at, $7),
			completed_at = COALESCE($8, completed_at),
			result = COALESCE($9::jsonb, result),
			error = COALESCE($10, error),
			next_check_at = COALESCE($11, next_check_at),
			updated_at = now()
		WHERE organization_id = $1 AND id = $2 AND state = ANY($3)
		RETURNING `+commandColumns,
		organizationID, id, u.From, state, boolInt(u.IncAttempts), u.DispatchedAt, u.ReceivedAt, u.CompletedAt,
		result, u.Error, u.NextCheckAt)
	updated, err := scanCommand(row)
	if err == nil {
		return updated, true, nil
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, false, err
	}
	current, err := p.GetCommand(ctx, organizationID, id)
	return current, false, err
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func (p *Postgres) collectCommands(rows pgx.Rows, err error) ([]*Command, error) {
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Command
	for rows.Next() {
		c, err := scanCommand(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func (p *Postgres) ClaimDueCommands(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]*Command, error) {
	return p.collectCommands(p.pool.Query(ctx, `UPDATE dataplane.commands SET next_check_at = $2
		WHERE id IN (
			SELECT id FROM dataplane.commands
			WHERE state IN ('PENDING','DISPATCHED','RECEIVED','EXECUTING') AND next_check_at <= $1
			ORDER BY next_check_at LIMIT $3 FOR UPDATE SKIP LOCKED)
		RETURNING `+commandColumns, now, now.Add(lease), limit))
}

func (p *Postgres) UndeliveredCommands(ctx context.Context, organizationID, applicationID, deviceID string, limit int) ([]*Command, error) {
	return p.collectCommands(p.pool.Query(ctx, `SELECT `+commandColumns+` FROM dataplane.commands
		WHERE organization_id = $1 AND application_id = $2 AND state IN ('PENDING','DISPATCHED')
			AND (device_id = '' OR device_id = $3)
		ORDER BY created_at LIMIT $4`, organizationID, applicationID, deviceID, limit))
}
