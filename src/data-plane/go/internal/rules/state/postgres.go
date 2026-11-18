package state

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"meteorcloud/dataplane/internal/rules/eval"
)

// The engine owns the "rules" schema, like the data plane owns "dataplane": its own
// ordered migrations, applied once each under an advisory lock, and it never reads
// control-plane tables. The full record is kept as JSONB next to the columns that
// are queried or locked on.
var migrations = []string{
	`CREATE TABLE rules.rule_state (
		rule_id         text        NOT NULL,
		device_id       text        NOT NULL,
		organization_id text        NOT NULL,
		rule_version    integer     NOT NULL,
		condition_hash  text        NOT NULL,
		phase           text        NOT NULL,
		version         bigint      NOT NULL,
		updated_at      timestamptz NOT NULL DEFAULT now(),
		doc             jsonb       NOT NULL,
		PRIMARY KEY (rule_id, device_id)
	);
	CREATE INDEX rule_state_device ON rules.rule_state (device_id);
	CREATE INDEX rule_state_firing ON rules.rule_state (organization_id, rule_id) WHERE phase = 'firing';
	CREATE TABLE rules.device_cursor (
		device_id       text        PRIMARY KEY,
		organization_id text        NOT NULL,
		version         bigint      NOT NULL,
		updated_at      timestamptz NOT NULL DEFAULT now(),
		doc             jsonb       NOT NULL
	);
	CREATE TABLE rules.rule_timers (
		rule_id         text        NOT NULL,
		device_id       text        NOT NULL,
		organization_id text        NOT NULL,
		due_at          timestamptz NOT NULL,
		PRIMARY KEY (rule_id, device_id)
	);
	CREATE INDEX rule_timers_due ON rules.rule_timers (due_at);
	CREATE TABLE rules.action_outbox (
		id               bigserial   PRIMARY KEY,
		idempotency_key  text        NOT NULL UNIQUE,
		organization_id  text        NOT NULL,
		rule_id          text        NOT NULL,
		rule_version     integer     NOT NULL,
		device_id        text        NOT NULL,
		transition       text        NOT NULL,
		action_type      text        NOT NULL,
		payload          jsonb       NOT NULL,
		status           text        NOT NULL DEFAULT 'pending',
		attempts         integer     NOT NULL DEFAULT 0,
		next_attempt_at  timestamptz NOT NULL,
		last_error       text,
		created_at       timestamptz NOT NULL,
		updated_at       timestamptz NOT NULL DEFAULT now()
	);
	CREATE INDEX action_outbox_due ON rules.action_outbox (next_attempt_at) WHERE status = 'pending';
	CREATE TABLE rules.action_dead_letters (
		id               bigint      PRIMARY KEY,
		idempotency_key  text        NOT NULL UNIQUE,
		organization_id  text        NOT NULL,
		rule_id          text        NOT NULL,
		device_id        text        NOT NULL,
		action_type      text        NOT NULL,
		payload          jsonb       NOT NULL,
		attempts         integer     NOT NULL,
		last_error       text        NOT NULL,
		failed_at        timestamptz NOT NULL DEFAULT now()
	);`,
}

// Postgres is the durable Store.
type Postgres struct {
	pool *pgxpool.Pool
}

// OpenPostgres connects and applies pending migrations of the "rules" schema.
func OpenPostgres(ctx context.Context, url string, maxConns int32) (*Postgres, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("parse rules database url: %w", err)
	}
	if maxConns > 0 {
		cfg.MaxConns = maxConns
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	p := &Postgres{pool: pool}
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
	if _, err := conn.Exec(ctx, `SELECT pg_advisory_lock(778899002)`); err != nil {
		return err
	}
	defer conn.Exec(context.Background(), `SELECT pg_advisory_unlock(778899002)`)
	if _, err := conn.Exec(ctx, `CREATE SCHEMA IF NOT EXISTS rules;
		CREATE TABLE IF NOT EXISTS rules.schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return fmt.Errorf("prepare rules migrations: %w", err)
	}
	var current int
	if err := conn.QueryRow(ctx, `SELECT COALESCE(MAX(version), 0) FROM rules.schema_migrations`).Scan(&current); err != nil {
		return err
	}
	for i := current; i < len(migrations); i++ {
		err := pgx.BeginFunc(ctx, conn, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, migrations[i]); err != nil {
				return fmt.Errorf("rules migration %d: %w", i+1, err)
			}
			_, err := tx.Exec(ctx, `INSERT INTO rules.schema_migrations (version) VALUES ($1)`, i+1)
			return err
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func (p *Postgres) Ping(ctx context.Context) error { return p.pool.Ping(ctx) }
func (p *Postgres) Close()                         { p.pool.Close() }

func (p *Postgres) LoadCursor(ctx context.Context, deviceID string) (*Cursor, error) {
	var doc []byte
	var version int64
	err := p.pool.QueryRow(ctx, `SELECT doc, version FROM rules.device_cursor WHERE device_id = $1`, deviceID).Scan(&doc, &version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var c Cursor
	if err := json.Unmarshal(doc, &c); err != nil {
		return nil, fmt.Errorf("decode cursor: %w", err)
	}
	c.Version = version
	return &c, nil
}

func (p *Postgres) LoadStates(ctx context.Context, deviceID string, ruleIDs []string) (map[string]*RuleState, error) {
	rows, err := p.pool.Query(ctx, `SELECT rule_id, doc, version FROM rules.rule_state WHERE device_id = $1 AND rule_id = ANY($2)`, deviceID, ruleIDs)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]*RuleState{}
	for rows.Next() {
		var id string
		var doc []byte
		var version int64
		if err := rows.Scan(&id, &doc, &version); err != nil {
			return nil, err
		}
		s, err := decodeState(doc, version)
		if err != nil {
			return nil, err
		}
		out[id] = s
	}
	return out, rows.Err()
}

func decodeState(doc []byte, version int64) (*RuleState, error) {
	var s RuleState
	if err := json.Unmarshal(doc, &s); err != nil {
		return nil, fmt.Errorf("decode rule state: %w", err)
	}
	if s.Nodes == nil {
		s.Nodes = eval.State{}
	}
	s.Version = version
	return &s, nil
}

// CommitMany writes all batches in one transaction, each inside its own savepoint, so
// a conflict in one batch rolls back only that batch.
func (p *Postgres) CommitMany(ctx context.Context, batches []*Batch) []error {
	errs := make([]error, len(batches))
	fail := func(err error) []error {
		for i := range errs {
			errs[i] = err
		}
		return errs
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return fail(err)
	}
	defer tx.Rollback(context.Background())
	applied := make([]bool, len(batches))
	for i, b := range batches {
		if _, err := tx.Exec(ctx, `SAVEPOINT b`); err != nil {
			return fail(err)
		}
		if err := applyBatch(ctx, tx, b); err != nil {
			if _, rbErr := tx.Exec(ctx, `ROLLBACK TO SAVEPOINT b`); rbErr != nil {
				return fail(rbErr)
			}
			errs[i] = err
			continue
		}
		if _, err := tx.Exec(ctx, `RELEASE SAVEPOINT b`); err != nil {
			return fail(err)
		}
		applied[i] = true
	}
	if err := tx.Commit(ctx); err != nil {
		return fail(err)
	}
	// Advance in-memory versions only after the commit is durable.
	for i, b := range batches {
		if !applied[i] {
			continue
		}
		if b.Cursor != nil {
			b.Cursor.Version++
		}
		for _, s := range b.States {
			s.Version++
		}
	}
	return errs
}

func applyBatch(ctx context.Context, tx pgx.Tx, b *Batch) error {
	if c := b.Cursor; c != nil {
		doc, _ := json.Marshal(c)
		var tag interface{ RowsAffected() int64 }
		var err error
		if c.Version == 0 {
			tag, err = tx.Exec(ctx, `INSERT INTO rules.device_cursor (device_id, organization_id, version, doc)
				VALUES ($1, $2, 1, $3) ON CONFLICT DO NOTHING`, c.DeviceID, c.OrgID, doc)
		} else {
			tag, err = tx.Exec(ctx, `UPDATE rules.device_cursor SET version = version + 1, doc = $3, updated_at = now()
				WHERE device_id = $1 AND version = $2`, c.DeviceID, c.Version, doc)
		}
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return ErrConflict
		}
	}
	for _, s := range b.States {
		doc, _ := json.Marshal(s)
		var tag interface{ RowsAffected() int64 }
		var err error
		if s.Version == 0 {
			tag, err = tx.Exec(ctx, `INSERT INTO rules.rule_state
				(rule_id, device_id, organization_id, rule_version, condition_hash, phase, version, doc)
				VALUES ($1, $2, $3, $4, $5, $6, 1, $7) ON CONFLICT DO NOTHING`,
				s.RuleID, s.DeviceID, s.OrgID, s.RuleVersion, s.ConditionHash, s.Phase, doc)
		} else {
			tag, err = tx.Exec(ctx, `UPDATE rules.rule_state SET rule_version = $4, condition_hash = $5, phase = $6,
				doc = $7, version = version + 1, updated_at = now()
				WHERE rule_id = $1 AND device_id = $2 AND version = $3`,
				s.RuleID, s.DeviceID, s.Version, s.RuleVersion, s.ConditionHash, s.Phase, doc)
		}
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return ErrConflict
		}
	}
	for _, s := range b.Deleted {
		tag, err := tx.Exec(ctx, `DELETE FROM rules.rule_state WHERE rule_id = $1 AND device_id = $2 AND version = $3`,
			s.RuleID, s.DeviceID, s.Version)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM rules.rule_state WHERE rule_id = $1 AND device_id = $2)`,
				s.RuleID, s.DeviceID).Scan(&exists); err != nil {
				return err
			}
			if exists {
				return ErrConflict
			}
		}
		if _, err := tx.Exec(ctx, `DELETE FROM rules.rule_timers WHERE rule_id = $1 AND device_id = $2`, s.RuleID, s.DeviceID); err != nil {
			return err
		}
	}
	for _, k := range b.TimersClear {
		if _, err := tx.Exec(ctx, `DELETE FROM rules.rule_timers WHERE rule_id = $1 AND device_id = $2`, k.RuleID, k.DeviceID); err != nil {
			return err
		}
	}
	for _, t := range b.TimersSet {
		if _, err := tx.Exec(ctx, `INSERT INTO rules.rule_timers (rule_id, device_id, organization_id, due_at) VALUES ($1, $2, $3, $4)
			ON CONFLICT (rule_id, device_id) DO UPDATE SET due_at = EXCLUDED.due_at`, t.RuleID, t.DeviceID, t.OrgID, t.Due); err != nil {
			return err
		}
	}
	for _, o := range b.Outbox {
		status := o.Status
		if status == "" {
			status = OutboxPending
		}
		if _, err := tx.Exec(ctx, `INSERT INTO rules.action_outbox
			(idempotency_key, organization_id, rule_id, rule_version, device_id, transition, action_type, payload, status, next_attempt_at, created_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT (idempotency_key) DO NOTHING`,
			o.Key, o.OrgID, o.RuleID, o.RuleVersion, o.DeviceID, o.Transition, o.ActionType, []byte(o.Payload), status,
			o.NextAttempt, o.CreatedAt); err != nil {
			return err
		}
	}
	return nil
}

func (p *Postgres) ClaimTimers(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]Timer, error) {
	rows, err := p.pool.Query(ctx, `
		UPDATE rules.rule_timers t SET due_at = $2
		FROM (SELECT rule_id, device_id, due_at FROM rules.rule_timers WHERE due_at <= $1
		      ORDER BY due_at LIMIT $3 FOR UPDATE SKIP LOCKED) d
		WHERE t.rule_id = d.rule_id AND t.device_id = d.device_id
		RETURNING t.rule_id, t.device_id, t.organization_id, d.due_at`, now, now.Add(lease), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Timer
	for rows.Next() {
		var t Timer
		if err := rows.Scan(&t.RuleID, &t.DeviceID, &t.OrgID, &t.Due); err != nil {
			return nil, err
		}
		t.Due = t.Due.UTC()
		out = append(out, t)
	}
	return out, rows.Err()
}

func (p *Postgres) StatesForRule(ctx context.Context, ruleID, after string, limit int) ([]*RuleState, error) {
	rows, err := p.pool.Query(ctx, `SELECT doc, version FROM rules.rule_state WHERE rule_id = $1 AND device_id > $2
		ORDER BY device_id LIMIT $3`, ruleID, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*RuleState
	for rows.Next() {
		var doc []byte
		var version int64
		if err := rows.Scan(&doc, &version); err != nil {
			return nil, err
		}
		s, err := decodeState(doc, version)
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

func (p *Postgres) Counts(ctx context.Context) (int, int, error) {
	var timers, outbox int
	err := p.pool.QueryRow(ctx, `SELECT (SELECT count(*) FROM rules.rule_timers),
		(SELECT count(*) FROM rules.action_outbox WHERE status = 'pending')`).Scan(&timers, &outbox)
	return timers, outbox, err
}

// Outbox lists the outbox, oldest first (tests and operators).
func (p *Postgres) Outbox(ctx context.Context, limit int) ([]OutboxItem, error) {
	rows, err := p.pool.Query(ctx, `SELECT id, idempotency_key, organization_id, rule_id, rule_version, device_id, transition,
		action_type, payload, status, attempts, next_attempt_at, COALESCE(last_error, ''), created_at
		FROM rules.action_outbox ORDER BY id LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []OutboxItem
	for rows.Next() {
		var o OutboxItem
		var payload []byte
		if err := rows.Scan(&o.ID, &o.Key, &o.OrgID, &o.RuleID, &o.RuleVersion, &o.DeviceID, &o.Transition, &o.ActionType,
			&payload, &o.Status, &o.Attempts, &o.NextAttempt, &o.LastError, &o.CreatedAt); err != nil {
			return nil, err
		}
		o.Payload = payload
		out = append(out, o)
	}
	return out, rows.Err()
}
