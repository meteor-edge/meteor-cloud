package storage

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/envelope"
)

func clickhouseForTest(t *testing.T, retentionDays int) *ClickHouse {
	url := os.Getenv("DATAPLANE_TEST_CLICKHOUSE_URL")
	if url == "" {
		t.Skip("DATAPLANE_TEST_CLICKHOUSE_URL is not set")
	}
	ch, err := OpenClickHouse(context.Background(), ClickHouseOptions{URL: url, Database: "dataplane_test",
		Password: os.Getenv("DATAPLANE_TEST_CLICKHOUSE_PASSWORD"), QueryTimeout: 5 * time.Second, RetentionDays: retentionDays})
	if err != nil {
		t.Fatalf("clickhouse: %v", err)
	}
	t.Cleanup(ch.Close)
	return ch
}

func TestClickHouseFiltersAndHostileValues(t *testing.T) {
	ch := clickhouseForTest(t, 0)
	ctx := context.Background()
	// Values that would break out of a statement if they were pasted into SQL.
	org := "org-" + uuid.NewString() + "' OR '1'='1"
	hostile := "id\t'\\\n\"x"
	base := time.Date(2026, 11, 12, 9, 0, 0, 123456000, time.UTC)
	seq := int64(9007199254740993) // does not fit a float64
	first := event(org, hostile, "energy.power", base, `{ "n": 1.5, "ok": true, "none": null, "tags": ["a", "b"], "s": "it's" }`)
	first.Seq = &seq
	batch := []*envelope.Event{first, event(org, "plain", "energy.power", base.Add(time.Second), `{"n":"1.5","ok":"true","tags":["a"]}`)}
	inserted, err := ch.InsertEvents(ctx, batch)
	if err != nil || !inserted[0] || !inserted[1] {
		t.Fatalf("insert: %v %v", inserted, err)
	}
	if again, err := ch.InsertEvents(ctx, batch); err != nil || again[0] || again[1] {
		t.Fatalf("redelivery must be a no-op: %v %v", again, err)
	}
	for name, filter := range map[string]string{
		"number": `{"n":1.5}`, "bool": `{"ok":true}`, "null": `{"none":null}`, "array": `{"tags":["a","b"]}`,
		"string": `{"s":"it's"}`, "two": `{"n":1.5,"ok":true}`,
	} {
		var f map[string]json.RawMessage
		json.Unmarshal([]byte(filter), &f)
		got, _, err := ch.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10, Filter: f})
		if err != nil || len(got) != 1 || got[0].ID != hostile {
			t.Fatalf("%s filter: %+v %v", name, got, err)
		}
	}
	all, _, err := ch.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10})
	if err != nil || len(all) != 2 {
		t.Fatalf("query: %+v %v", all, err)
	}
	if !all[0].Time.Equal(base) || all[0].Seq == nil || *all[0].Seq != seq || all[0].SchemaVersion != 1 {
		t.Fatalf("time, seq or version lost: %+v", all[0])
	}
	if _, _, err := ch.QueryEvents(ctx, Query{OrganizationID: strings.Split(org, "'")[0], Stream: "energy.power", Limit: 10}); err != nil {
		t.Fatal(err)
	}
}

func TestClickHouseRetentionIsATableTTL(t *testing.T) {
	ddl := func(ch *ClickHouse) string {
		var row struct {
			DDL string `json:"create_table_query"`
		}
		err := ch.query(context.Background(), `SELECT create_table_query FROM system.tables WHERE database = 'dataplane_test' AND name = 'events'`,
			nil, func(line []byte) error { return json.Unmarshal(line, &row) })
		if err != nil {
			t.Fatal(err)
		}
		return row.DDL
	}
	if got := ddl(clickhouseForTest(t, 30)); !strings.Contains(got, "toIntervalDay(30)") {
		t.Fatalf("TTL not set: %s", got)
	}
	if got := ddl(clickhouseForTest(t, 7)); !strings.Contains(got, "toIntervalDay(7)") || strings.Contains(got, "toIntervalDay(30)") {
		t.Fatalf("TTL not changed: %s", got)
	}
	if got := ddl(clickhouseForTest(t, 0)); strings.Contains(got, " TTL ") {
		t.Fatalf("TTL not removed: %s", got)
	}
}

func TestClickHouseRejectsBadSettings(t *testing.T) {
	ctx := context.Background()
	if _, err := OpenClickHouse(ctx, ClickHouseOptions{URL: "clickhouse:9000", Database: "x"}); err == nil {
		t.Fatal("a url without http(s) must be refused")
	}
	if _, err := OpenClickHouse(ctx, ClickHouseOptions{URL: "http://127.0.0.1:1", Database: "x; DROP"}); err == nil {
		t.Fatal("an unsafe database name must be refused")
	}
	if url := os.Getenv("DATAPLANE_TEST_CLICKHOUSE_URL"); url != "" {
		_, err := OpenClickHouse(ctx, ClickHouseOptions{URL: url, Database: "dataplane_test", Password: "wrong-" + uuid.NewString()})
		if err == nil || strings.Contains(err.Error(), "wrong-") {
			t.Fatalf("wrong password must fail without echoing it: %v", err)
		}
	}
}
