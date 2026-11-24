package storage

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

// Device metrics in ClickHouse.
//
// The agent publishes one JSON object per minute on devices/<id>/metrics, for
// example {"cpu_percent": 12.5, "memory_percent": 40.1, "disk_percent": 71.0,
// "temperature_c": 48.2}. device-ingest puts it on JetStream and the ClickHouse
// writer stores it in the events table like every other event. A materialized view
// then copies every numeric top-level key into device_metrics, one row per key, so
// charts read a narrow table sorted by device and metric instead of parsing JSON.
// New keys sent by a newer agent appear without a schema change.
//
// ReplacingMergeTree on (organization, device, metric, time, event id) folds the
// duplicates that the one-time backfill below, or two writer replicas, can create.
// Until a merge has run, the chart query folds them itself (one sample per event
// id), so avg and count are not skewed.

const (
	// About 20 system keys plus up to 20 apps with 6 keys each (agent 0.3.4).
	deviceMetricsMaxKeys   = 160
	deviceMetricsMaxKeyLen = 64
)

const clickhouseDeviceMetricsDDL = `CREATE TABLE IF NOT EXISTS %s.device_metrics (
	organization_id String,
	device_id       String,
	name            LowCardinality(String),
	ts              DateTime64(6, 'UTC'),
	event_id        String,
	value           Float64 CODEC(Gorilla, ZSTD(1))
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (organization_id, device_id, name, ts, event_id)`

// deviceMetricsSelect turns events rows into device_metrics rows. %[1]s is the
// database; %[2]s is an extra condition.
const deviceMetricsSelect = `SELECT organization_id, device_id, k AS name, ts, event_id, JSONExtractFloat(data, k) AS value
	FROM %[1]s.events
	ARRAY JOIN arraySlice(arrayFilter(x -> length(x) <= %[3]d AND JSONType(data, x) IN ('Int64', 'UInt64', 'Double'),
		JSONExtractKeys(data)), 1, %[4]d) AS k
	WHERE stream = '` + DeviceMetricsStream + `' AND device_id != '' %[2]s`

func (c *ClickHouse) deviceMetricsSelect(extra string) string {
	return fmt.Sprintf(deviceMetricsSelect, c.database, extra, deviceMetricsMaxKeyLen, deviceMetricsMaxKeys)
}

// ensureDeviceMetrics creates the table and the view. The first time, it copies
// the metrics already in events, creates the view, and copies again what arrived
// in between. The view is created after the first copy, so a failed copy is
// retried on the next start; the catch-up copy after the view is recorded in
// device_metrics_backfill, so it is retried too until it has succeeded once.
func (c *ClickHouse) ensureDeviceMetrics(ctx context.Context) error {
	if err := c.exec(ctx, fmt.Sprintf(clickhouseDeviceMetricsDDL, c.database), nil, nil); err != nil {
		return err
	}
	if err := c.exec(ctx, "CREATE TABLE IF NOT EXISTS "+c.database+
		".device_metrics_backfill (done_at DateTime64(6, 'UTC')) ENGINE = MergeTree ORDER BY tuple()", nil, nil); err != nil {
		return err
	}
	done := false
	if err := c.query(ctx, "SELECT 1 AS x FROM "+c.database+".device_metrics_backfill LIMIT 1", nil,
		func([]byte) error { done = true; return nil }); err != nil {
		return err
	}
	var viewCreated string
	err := c.query(ctx, `SELECT toString(metadata_modification_time) AS t FROM system.tables
		WHERE database = {db:String} AND name = 'device_metrics_mv'`,
		map[string]string{"db": c.database}, func(line []byte) error {
			var r struct {
				T string `json:"t"`
			}
			if err := json.Unmarshal(line, &r); err != nil {
				return err
			}
			viewCreated = r.T
			return nil
		})
	if err != nil {
		return err
	}
	if viewCreated != "" && done {
		return nil
	}
	since := time.Now().Add(-5 * time.Minute)
	if viewCreated == "" {
		if err := c.exec(ctx, "INSERT INTO "+c.database+".device_metrics "+c.deviceMetricsSelect(""), nil, nil); err != nil {
			return fmt.Errorf("backfill: %w", err)
		}
		view := "CREATE MATERIALIZED VIEW IF NOT EXISTS " + c.database + ".device_metrics_mv TO " + c.database + ".device_metrics AS " +
			c.deviceMetricsSelect("")
		if err := c.exec(ctx, view, nil, nil); err != nil {
			return err
		}
	} else if t, err := time.ParseInLocation("2006-01-02 15:04:05", viewCreated, time.UTC); err == nil {
		// A previous start created the view but did not finish the catch-up copy.
		since = t.Add(-5 * time.Minute)
	} else {
		since = time.Unix(0, 0)
	}
	if err := c.exec(ctx, "INSERT INTO "+c.database+".device_metrics "+
		c.deviceMetricsSelect("AND received_at >= {since:DateTime64(6, 'UTC')}"), map[string]string{"since": chTime(since)}, nil); err != nil {
		return fmt.Errorf("backfill catch-up: %w", err)
	}
	return c.exec(ctx, "INSERT INTO "+c.database+".device_metrics_backfill (done_at) VALUES (now64(6))", nil, nil)
}

// DeviceMetrics reads all requested metrics of one device in one query.
func (c *ClickHouse) DeviceMetrics(ctx context.Context, q DeviceMetricsQuery) ([]MetricSeries, error) {
	width := q.Bucket.Microseconds()
	if width < time.Second.Microseconds() {
		return nil, fmt.Errorf("bucket is too small")
	}
	params := map[string]string{
		"org": chString(q.OrganizationID), "dev": chString(q.DeviceID), "names": chArray(q.Names),
		"start": chTime(q.Start), "end": chTime(q.End),
	}
	// The inner query keeps one sample per event id: duplicates that have not
	// merged yet would otherwise skew avg and count.
	sql := fmt.Sprintf(`SELECT name, intDiv(toUnixTimestamp64Micro(ts), %d) * %d AS bucket,
			avg(value) AS avg, min(value) AS min, max(value) AS max, count() AS n
		FROM (
			SELECT name, ts, any(value) AS value FROM %s.device_metrics
			WHERE organization_id = {org:String} AND device_id = {dev:String} AND name IN {names:Array(String)}
				AND ts >= {start:DateTime64(6, 'UTC')} AND ts < {end:DateTime64(6, 'UTC')}
			GROUP BY name, ts, event_id
		)
		GROUP BY name, bucket ORDER BY name, bucket`, width, width, c.database)
	index := map[string]int{}
	var out []MetricSeries
	err := c.query(ctx, sql, params, func(line []byte) error {
		var r struct {
			Name   string  `json:"name"`
			Bucket int64   `json:"bucket"`
			Avg    float64 `json:"avg"`
			Min    float64 `json:"min"`
			Max    float64 `json:"max"`
			N      int64   `json:"n"`
		}
		if err := json.Unmarshal(line, &r); err != nil {
			return err
		}
		i, ok := index[r.Name]
		if !ok {
			i = len(out)
			index[r.Name] = i
			out = append(out, MetricSeries{Name: r.Name})
		}
		out[i].Points = append(out[i].Points, MetricPoint{Start: time.UnixMicro(r.Bucket).UTC(), Avg: r.Avg, Min: r.Min, Max: r.Max, Count: r.N})
		return nil
	})
	return out, err
}

// CountDevices counts distinct devices per bucket in one query.
func (c *ClickHouse) CountDevices(ctx context.Context, q Query, bucket time.Duration) ([]DeviceCount, error) {
	width := bucket.Microseconds()
	if width < time.Second.Microseconds() {
		return nil, fmt.Errorf("bucket is too small")
	}
	params := map[string]string{}
	where, err := c.where(&q, params)
	if err != nil {
		return nil, err
	}
	sql := fmt.Sprintf(`SELECT intDiv(toUnixTimestamp64Micro(ts), %d) * %d AS bucket, uniqExact(device_id) AS n
		FROM %s.events WHERE %s AND device_id != '' GROUP BY bucket ORDER BY bucket`, width, width, c.database, where)
	var out []DeviceCount
	err = c.query(ctx, sql, params, func(line []byte) error {
		var r struct {
			Bucket int64 `json:"bucket"`
			N      int64 `json:"n"`
		}
		if err := json.Unmarshal(line, &r); err != nil {
			return err
		}
		out = append(out, DeviceCount{Start: time.UnixMicro(r.Bucket).UTC(), Devices: r.N})
		return nil
	})
	return out, err
}
