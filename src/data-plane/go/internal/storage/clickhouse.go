package storage

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/envelope"
)

// ClickHouse stores events (telemetry, events, inference) in ClickHouse over its HTTP
// interface. It is an EventStore: commands and dead letters need row updates and
// stay in PostgreSQL (see Split).
//
// Durability: an insert returns only after ClickHouse has written the rows. With
// AsyncInsert the server groups many small inserts into one part and still answers
// only after the flush (wait_for_async_insert=1), so "stored" keeps its meaning while
// thousands of small publishes do not create thousands of parts.
//
// Idempotency: before inserting, the ids of the batch are looked up through the
// by_id projection and known ids are skipped. Two replicas that receive the same id
// at the same instant can both store it; a retry after either answer is a no-op.
type ClickHouse struct {
	base         string
	database     string
	user         string
	password     string
	asyncInsert  bool
	queryTimeout time.Duration
	http         *http.Client
}

// ClickHouseOptions configures OpenClickHouse.
type ClickHouseOptions struct {
	URL           string // for example http://clickhouse:8123
	Database      string
	User          string
	Password      string
	AsyncInsert   bool
	QueryTimeout  time.Duration
	RetentionDays int // 0 keeps events forever
}

var clickhouseIdent = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,62}$`)

const clickhouseEventsDDL = `CREATE TABLE IF NOT EXISTS %s.events (
	organization_id String,
	event_id        String,
	stream          LowCardinality(String),
	kind            LowCardinality(String),
	type            LowCardinality(String),
	application_id  String,
	application     LowCardinality(String),
	device_id       String,
	environment     LowCardinality(String),
	ts              DateTime64(6, 'UTC'),
	received_at     DateTime64(6, 'UTC'),
	seq             Nullable(Int64),
	schema_version  UInt16,
	data            String CODEC(ZSTD(3)),
	PROJECTION by_id (SELECT organization_id, event_id ORDER BY organization_id, event_id)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (organization_id, stream, ts, event_id)`

// OpenClickHouse checks the connection, creates the database and table when missing
// and applies the retention period as a table TTL.
func OpenClickHouse(ctx context.Context, o ClickHouseOptions) (*ClickHouse, error) {
	u, err := url.Parse(o.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("clickhouse url must be http(s)://host:port")
	}
	if !clickhouseIdent.MatchString(o.Database) {
		return nil, fmt.Errorf("clickhouse database name is invalid")
	}
	if o.User == "" {
		o.User = "default"
	}
	c := &ClickHouse{
		base: u.Scheme + "://" + u.Host, database: o.Database, user: o.User, password: o.Password,
		asyncInsert: o.AsyncInsert, queryTimeout: o.QueryTimeout,
		http: &http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: 32, IdleConnTimeout: 90 * time.Second}},
	}
	if err := c.Ping(ctx); err != nil {
		return nil, err
	}
	if err := c.exec(ctx, "CREATE DATABASE IF NOT EXISTS "+c.database, nil, nil); err != nil {
		return nil, fmt.Errorf("create database: %w", err)
	}
	if err := c.exec(ctx, fmt.Sprintf(clickhouseEventsDDL, c.database), nil, nil); err != nil {
		return nil, fmt.Errorf("create events table: %w", err)
	}
	if err := c.applyRetention(ctx, o.RetentionDays); err != nil {
		return nil, fmt.Errorf("retention: %w", err)
	}
	return c, nil
}

// applyRetention sets or removes the table TTL, only when it differs from the
// current one so a restart does not rewrite data.
func (c *ClickHouse) applyRetention(ctx context.Context, days int) error {
	var row struct {
		DDL string `json:"create_table_query"`
	}
	err := c.query(ctx, `SELECT create_table_query FROM system.tables WHERE database = {db:String} AND name = 'events'`,
		map[string]string{"db": c.database}, func(line []byte) error { return json.Unmarshal(line, &row) })
	if err != nil {
		return err
	}
	has := strings.Contains(row.DDL, " TTL ")
	switch {
	case days <= 0 && has:
		return c.exec(ctx, "ALTER TABLE "+c.database+".events REMOVE TTL", nil, nil)
	case days > 0 && !strings.Contains(row.DDL, fmt.Sprintf("toIntervalDay(%d)", days)):
		return c.exec(ctx, fmt.Sprintf("ALTER TABLE %s.events MODIFY TTL toDateTime(ts) + toIntervalDay(%d)", c.database, days), nil, nil)
	}
	return nil
}

func (c *ClickHouse) Ping(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/ping", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("clickhouse unreachable: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("clickhouse ping: status %d", resp.StatusCode)
	}
	return nil
}

func (c *ClickHouse) Close() { c.http.CloseIdleConnections() }

// do sends one statement. params become typed query parameters ({name:Type} in the
// statement), so values never become part of the SQL text.
func (c *ClickHouse) do(ctx context.Context, sql string, params, settings map[string]string, body io.Reader) (*http.Response, error) {
	v := url.Values{}
	v.Set("database", "default")
	v.Set("wait_end_of_query", "1") // errors arrive as a status code, never mid-stream
	for k, val := range params {
		v.Set("param_"+k, val)
	}
	for k, val := range settings {
		v.Set(k, val)
	}
	if body == nil {
		body = strings.NewReader(sql)
	} else {
		v.Set("query", sql)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/?"+v.Encode(), body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-ClickHouse-User", c.user)
	if c.password != "" {
		req.Header.Set("X-ClickHouse-Key", c.password)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		resp.Body.Close()
		return nil, fmt.Errorf("clickhouse: status %d: %s", resp.StatusCode, strings.TrimSpace(string(msg)))
	}
	return resp, nil
}

func (c *ClickHouse) exec(ctx context.Context, sql string, params, settings map[string]string) error {
	resp, err := c.do(ctx, sql, params, settings, nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, err = io.Copy(io.Discard, resp.Body)
	return err
}

// query runs a SELECT and calls row for every JSONEachRow line.
func (c *ClickHouse) query(ctx context.Context, sql string, params map[string]string, row func(line []byte) error) error {
	settings := map[string]string{"output_format_json_quote_64bit_integers": "0", "readonly": "2"}
	if c.queryTimeout > 0 {
		settings["max_execution_time"] = strconv.FormatFloat(c.queryTimeout.Seconds(), 'f', 3, 64)
	}
	resp, err := c.do(ctx, sql+" FORMAT JSONEachRow", params, settings, nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	r := bufio.NewReaderSize(resp.Body, 64<<10)
	for {
		line, err := r.ReadBytes('\n')
		if len(bytes.TrimSpace(line)) > 0 {
			if rerr := row(line); rerr != nil {
				return rerr
			}
		}
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

// chString escapes a value for a String query parameter.
func chString(s string) string {
	return strings.NewReplacer(`\`, `\\`, "\t", `\t`, "\n", `\n`, "\r", `\r`).Replace(s)
}

// chArray formats values for an Array(String) query parameter.
func chArray(values []string) string {
	var b strings.Builder
	b.WriteByte('[')
	for i, v := range values {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteByte('\'')
		b.WriteString(strings.NewReplacer(`\`, `\\`, `'`, `\'`, "\t", `\t`, "\n", `\n`, "\r", `\r`).Replace(v))
		b.WriteByte('\'')
	}
	b.WriteByte(']')
	return b.String()
}

const chTimeLayout = "2006-01-02 15:04:05.000000"

func chTime(t time.Time) string { return t.UTC().Format(chTimeLayout) }

type chEventRow struct {
	OrganizationID string `json:"organization_id"`
	EventID        string `json:"event_id"`
	Stream         string `json:"stream"`
	Kind           string `json:"kind"`
	Type           string `json:"type"`
	ApplicationID  string `json:"application_id"`
	Application    string `json:"application"`
	DeviceID       string `json:"device_id"`
	Environment    string `json:"environment"`
	TS             string `json:"ts,omitempty"`
	ReceivedAt     string `json:"received_at,omitempty"`
	TSMicro        int64  `json:"ts_us,omitempty"`
	ReceivedMicro  int64  `json:"received_us,omitempty"`
	Seq            *int64 `json:"seq"`
	SchemaVersion  int    `json:"schema_version"`
	Data           string `json:"data"`
}

// existing returns the ids of one organization that are already stored.
func (c *ClickHouse) existing(ctx context.Context, org string, ids []string) (map[string]struct{}, error) {
	found := map[string]struct{}{}
	err := c.query(ctx, `SELECT event_id FROM `+c.database+`.events
		WHERE organization_id = {org:String} AND event_id IN {ids:Array(String)}`,
		map[string]string{"org": chString(org), "ids": chArray(ids)},
		func(line []byte) error {
			var r struct {
				EventID string `json:"event_id"`
			}
			if err := json.Unmarshal(line, &r); err != nil {
				return err
			}
			found[r.EventID] = struct{}{}
			return nil
		})
	return found, err
}

func (c *ClickHouse) InsertEvents(ctx context.Context, events []*envelope.Event) ([]bool, error) {
	inserted := make([]bool, len(events))
	byOrg := map[string][]string{}
	for _, ev := range events {
		byOrg[ev.OrganizationID] = append(byOrg[ev.OrganizationID], ev.ID)
	}
	known := map[string]struct{}{}
	for org, ids := range byOrg {
		found, err := c.existing(ctx, org, ids)
		if err != nil {
			return nil, err
		}
		for id := range found {
			known[org+"\x00"+id] = struct{}{}
		}
	}
	var body bytes.Buffer
	enc := json.NewEncoder(&body)
	for i, ev := range events {
		key := ev.OrganizationID + "\x00" + ev.ID
		if _, dup := known[key]; dup {
			continue
		}
		known[key] = struct{}{} // a duplicate inside the same batch counts once
		inserted[i] = true
		var data bytes.Buffer
		if err := json.Compact(&data, ev.Data); err != nil {
			return nil, fmt.Errorf("event %s: data is not JSON", ev.ID)
		}
		if err := enc.Encode(chEventRow{
			OrganizationID: ev.OrganizationID, EventID: ev.ID, Stream: ev.Stream, Kind: ev.Kind, Type: ev.Type,
			ApplicationID: ev.ApplicationID, Application: ev.Application, DeviceID: ev.DeviceID,
			Environment: ev.Environment, TS: chTime(ev.Time), ReceivedAt: chTime(ev.ReceivedAt), Seq: ev.Seq,
			SchemaVersion: ev.SchemaVersion, Data: data.String(),
		}); err != nil {
			return nil, err
		}
	}
	if body.Len() == 0 {
		return inserted, nil
	}
	settings := map[string]string{}
	if c.asyncInsert {
		settings["async_insert"] = "1"
		settings["wait_for_async_insert"] = "1"
	}
	resp, err := c.do(ctx, "INSERT INTO "+c.database+".events FORMAT JSONEachRow", nil, settings, &body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	return inserted, nil
}

// where builds the conditions of q with typed parameters.
func (c *ClickHouse) where(q *Query, params map[string]string) (string, error) {
	n := 0
	add := func(typ, value string) string {
		n++
		name := "w" + strconv.Itoa(n)
		params[name] = value
		return "{" + name + ":" + typ + "}"
	}
	conds := []string{"organization_id = " + add("String", chString(q.OrganizationID)), "stream = " + add("String", chString(q.Stream))}
	if q.Kind != "" {
		conds = append(conds, "kind = "+add("String", chString(q.Kind)))
	}
	if q.DeviceID != "" {
		conds = append(conds, "device_id = "+add("String", chString(q.DeviceID)))
	}
	if q.Application != "" {
		conds = append(conds, "application = "+add("String", chString(q.Application)))
	}
	if !q.Start.IsZero() {
		conds = append(conds, "ts >= "+add("DateTime64(6, 'UTC')", chTime(q.Start)))
	}
	if !q.End.IsZero() {
		conds = append(conds, "ts < "+add("DateTime64(6, 'UTC')", chTime(q.End)))
	}
	for key, want := range q.Filter {
		k := add("String", chString(key))
		var v any
		if err := json.Unmarshal(want, &v); err != nil {
			return "", fmt.Errorf("filter value for %q is not JSON", key)
		}
		switch t := v.(type) {
		case string:
			conds = append(conds, fmt.Sprintf("(JSONType(data, %s) = 'String' AND JSONExtractString(data, %s) = %s)", k, k, add("String", chString(t))))
		case float64:
			conds = append(conds, fmt.Sprintf("(JSONType(data, %s) IN ('Int64', 'UInt64', 'Double') AND JSONExtractFloat(data, %s) = %s)",
				k, k, add("Float64", strconv.FormatFloat(t, 'g', -1, 64))))
		case bool:
			b := "0"
			if t {
				b = "1"
			}
			conds = append(conds, fmt.Sprintf("(JSONType(data, %s) = 'Bool' AND JSONExtractBool(data, %s) = %s)", k, k, add("UInt8", b)))
		case nil:
			conds = append(conds, fmt.Sprintf("(JSONHas(data, %s) AND JSONType(data, %s) = 'Null')", k, k))
		default:
			// Objects and arrays match when their compact JSON text is the same.
			raw, _ := json.Marshal(t)
			conds = append(conds, fmt.Sprintf("JSONExtractRaw(data, %s) = %s", k, add("String", chString(string(raw)))))
		}
	}
	return strings.Join(conds, " AND "), nil
}

func (c *ClickHouse) QueryEvents(ctx context.Context, q Query) ([]*envelope.Event, *Cursor, error) {
	params := map[string]string{}
	where, err := c.where(&q, params)
	if err != nil {
		return nil, nil, err
	}
	order, cmp := "ASC", ">"
	if q.Descending {
		order, cmp = "DESC", "<"
	}
	if q.After != nil {
		params["after_ts"], params["after_id"] = chTime(q.After.Time), chString(q.After.ID)
		where += fmt.Sprintf(" AND (ts, event_id) %s ({after_ts:DateTime64(6, 'UTC')}, {after_id:String})", cmp)
	}
	sql := fmt.Sprintf(`SELECT event_id, stream, kind, type, organization_id, application_id, application, device_id,
		environment, toUnixTimestamp64Micro(ts) AS ts_us, toUnixTimestamp64Micro(received_at) AS received_us,
		seq, schema_version, data
		FROM %s.events WHERE %s ORDER BY ts %s, event_id %s LIMIT %d`, c.database, where, order, order, q.Limit+1)
	var out []*envelope.Event
	err = c.query(ctx, sql, params, func(line []byte) error {
		var r chEventRow
		if err := json.Unmarshal(line, &r); err != nil {
			return err
		}
		out = append(out, &envelope.Event{
			ID: r.EventID, SchemaVersion: r.SchemaVersion, Kind: r.Kind, Type: r.Type, Stream: r.Stream,
			OrganizationID: r.OrganizationID, ApplicationID: r.ApplicationID, Application: r.Application,
			DeviceID: r.DeviceID, Environment: r.Environment, Time: time.UnixMicro(r.TSMicro).UTC(),
			ReceivedAt: time.UnixMicro(r.ReceivedMicro).UTC(), Seq: r.Seq, Data: json.RawMessage(r.Data),
		})
		return nil
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

var clickhouseAggregates = map[string]string{
	"avg": "avg(v)", "min": "min(v)", "max": "max(v)", "sum": "sum(v)", "count": "toFloat64(count())",
}

func (c *ClickHouse) Aggregate(ctx context.Context, q AggregateQuery) ([]Bucket, error) {
	expr, ok := clickhouseAggregates[q.Func]
	if !ok {
		return nil, fmt.Errorf("unsupported aggregate %q", q.Func)
	}
	if q.Bucket < time.Microsecond {
		return nil, fmt.Errorf("bucket is too small")
	}
	params := map[string]string{}
	where, err := c.where(&q.Query, params)
	if err != nil {
		return nil, err
	}
	path := make([]string, len(q.Field))
	for i, part := range q.Field {
		name := "p" + strconv.Itoa(i)
		params[name] = chString(part)
		path[i] = "{" + name + ":String}"
	}
	args := "data"
	if len(path) > 0 {
		args += ", " + strings.Join(path, ", ")
	}
	width := q.Bucket.Microseconds()
	// Non-numeric values are ignored instead of failing the query.
	sql := fmt.Sprintf(`SELECT bucket, %s AS value, count(v) AS n FROM (
			SELECT intDiv(toUnixTimestamp64Micro(ts), %d) * %d AS bucket,
				if(JSONType(%s) IN ('Int64', 'UInt64', 'Double'), JSONExtractFloat(%s), NULL) AS v
			FROM %s.events WHERE %s
		) GROUP BY bucket ORDER BY bucket`, expr, width, width, args, args, c.database, where)
	var out []Bucket
	err = c.query(ctx, sql, params, func(line []byte) error {
		var r struct {
			Bucket int64    `json:"bucket"`
			Value  *float64 `json:"value"`
			N      int64    `json:"n"`
		}
		if err := json.Unmarshal(line, &r); err != nil {
			return err
		}
		b := Bucket{Start: time.UnixMicro(r.Bucket).UTC(), Value: r.Value, Count: r.N}
		if q.Func == "count" && r.Value != nil {
			b.Count = int64(*r.Value)
		}
		out = append(out, b)
		return nil
	})
	return out, err
}

// DeleteEventsBefore is a no-op: ClickHouse removes old events itself through the
// table TTL that OpenClickHouse sets from the retention period.
func (c *ClickHouse) DeleteEventsBefore(context.Context, time.Time) (int64, error) { return 0, nil }
