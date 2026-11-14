package storage

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/deviceingest"
	"meteorcloud/dataplane/internal/envelope"
)

func TestDeviceMetricsStreamMatchesIngest(t *testing.T) {
	if DeviceMetricsStream != deviceingest.StreamMetrics {
		t.Fatalf("storage reads %q but device-ingest writes %q", DeviceMetricsStream, deviceingest.StreamMetrics)
	}
}

func metricEvent(org, dev, id string, ts time.Time, data string) *envelope.Event {
	return &envelope.Event{ID: id, SchemaVersion: 1, Kind: envelope.KindTelemetry, Type: "device.metrics",
		Stream: DeviceMetricsStream, OrganizationID: org, DeviceID: dev, Time: ts, ReceivedAt: ts,
		Data: json.RawMessage(data)}
}

func TestDeviceMetricsBucketsEveryBackend(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			org, other := uuid.NewString(), uuid.NewString()
			dev, dev2 := uuid.NewString(), uuid.NewString()
			base := time.Date(2026, 11, 14, 10, 0, 0, 0, time.UTC)
			batch := []*envelope.Event{
				metricEvent(org, dev, "m1", base, `{"cpu_percent":10,"memory_percent":40.5,"temperature_c":null}`),
				metricEvent(org, dev, "m2", base.Add(time.Minute), `{"cpu_percent":30,"memory_percent":41.5,"disk_percent":"71"}`),
				metricEvent(org, dev, "m3", base.Add(5*time.Minute), `{"cpu_percent":50,"temperature_c":48.25}`),
				// Another device and another organization must not leak in.
				metricEvent(org, dev2, "m4", base, `{"cpu_percent":99}`),
				metricEvent(other, dev, "m5", base, `{"cpu_percent":99}`),
				// Another stream with the same keys is not device telemetry.
				event(org, "x1", "energy.power", base, `{"cpu_percent":99}`),
			}
			if _, err := s.InsertEvents(ctx, batch); err != nil {
				t.Fatal(err)
			}
			got, err := QueryDeviceMetrics(ctx, s, DeviceMetricsQuery{
				OrganizationID: org, DeviceID: dev, Names: []string{"cpu_percent", "memory_percent", "disk_percent", "temperature_c"},
				Start: base, End: base.Add(10 * time.Minute), Bucket: 5 * time.Minute,
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(got) != 4 || got[0].Name != "cpu_percent" || got[2].Name != "disk_percent" || got[3].Name != "temperature_c" {
				t.Fatalf("series order or names: %+v", got)
			}
			cpu := got[0].Points
			if len(cpu) != 2 || !cpu[0].Start.Equal(base) || cpu[0].Avg != 20 || cpu[0].Min != 10 || cpu[0].Max != 30 || cpu[0].Count != 2 ||
				!cpu[1].Start.Equal(base.Add(5*time.Minute)) || cpu[1].Avg != 50 {
				t.Fatalf("cpu: %+v", cpu)
			}
			if mem := got[1].Points; len(mem) != 1 || mem[0].Avg != 41 {
				t.Fatalf("memory: %+v", mem)
			}
			// A string and a null are not numbers.
			if disk := got[2].Points; len(disk) != 0 {
				t.Fatalf("disk must be empty: %+v", disk)
			}
			if temp := got[3].Points; len(temp) != 1 || temp[0].Avg != 48.25 || !temp[0].Start.Equal(base.Add(5*time.Minute)) {
				t.Fatalf("temperature: %+v", temp)
			}
			if _, err := QueryDeviceMetrics(ctx, s, DeviceMetricsQuery{OrganizationID: org, DeviceID: dev, Names: []string{"cpu_percent"},
				Start: base, End: base.Add(time.Minute), Bucket: time.Millisecond}); err == nil {
				t.Fatal("a sub-second bucket must be refused")
			}
		})
	}
}

// Metrics stored before the view existed are copied once when it is created.
func TestClickHouseDeviceMetricsBackfill(t *testing.T) {
	url := os.Getenv("DATAPLANE_TEST_CLICKHOUSE_URL")
	if url == "" {
		t.Skip("DATAPLANE_TEST_CLICKHOUSE_URL is not set")
	}
	ctx := context.Background()
	db := "dataplane_backfill_test"
	opts := ClickHouseOptions{URL: url, Database: db, Password: os.Getenv("DATAPLANE_TEST_CLICKHOUSE_PASSWORD"), QueryTimeout: 5 * time.Second}
	ch, err := OpenClickHouse(ctx, opts)
	if err != nil {
		t.Fatal(err)
	}
	defer ch.Close()
	if err := ch.exec(ctx, "DROP VIEW IF EXISTS "+db+".device_metrics_mv", nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := ch.exec(ctx, "TRUNCATE TABLE "+db+".device_metrics", nil, nil); err != nil {
		t.Fatal(err)
	}
	org, dev := uuid.NewString(), uuid.NewString()
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)
	if _, err := ch.InsertEvents(ctx, []*envelope.Event{metricEvent(org, dev, "old", base, `{"cpu_percent":12}`)}); err != nil {
		t.Fatal(err)
	}
	again, err := OpenClickHouse(ctx, opts)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	// Written after the view exists: captured by the view, not the copy.
	if _, err := again.InsertEvents(ctx, []*envelope.Event{metricEvent(org, dev, "new", base.Add(time.Minute), `{"cpu_percent":14}`)}); err != nil {
		t.Fatal(err)
	}
	got, err := again.DeviceMetrics(ctx, DeviceMetricsQuery{OrganizationID: org, DeviceID: dev, Names: []string{"cpu_percent"},
		Start: base, End: base.Add(time.Hour), Bucket: time.Hour})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || len(got[0].Points) != 1 || got[0].Points[0].Avg != 13 {
		t.Fatalf("backfill + view: %+v", got)
	}
}
