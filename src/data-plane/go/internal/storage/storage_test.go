package storage

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/envelope"
)

// The same behaviour is required of every backend. PostgreSQL runs when
// DATAPLANE_TEST_DATABASE_URL points at a disposable database and ClickHouse when
// DATAPLANE_TEST_CLICKHOUSE_URL points at a disposable server.
func stores(t *testing.T) map[string]Store {
	out := map[string]Store{"memory": NewMemory()}
	if url := os.Getenv("DATAPLANE_TEST_DATABASE_URL"); url != "" {
		pg, err := OpenPostgres(context.Background(), url, 4, 5*time.Second)
		if err != nil {
			t.Fatalf("postgres: %v", err)
		}
		t.Cleanup(pg.Close)
		out["postgres"] = pg
	}
	// ClickHouse holds the events; commands stay in the other store, as in production.
	if url := os.Getenv("DATAPLANE_TEST_CLICKHOUSE_URL"); url != "" {
		ch, err := OpenClickHouse(context.Background(), ClickHouseOptions{URL: url, Database: "dataplane_test",
			Password: os.Getenv("DATAPLANE_TEST_CLICKHOUSE_PASSWORD"), AsyncInsert: true, QueryTimeout: 5 * time.Second})
		if err != nil {
			t.Fatalf("clickhouse: %v", err)
		}
		split := &Split{Store: NewMemory(), Events: ch}
		t.Cleanup(split.Close)
		out["clickhouse"] = split
	}
	return out
}

func event(org, id, stream string, ts time.Time, data string) *envelope.Event {
	return &envelope.Event{ID: id, SchemaVersion: 1, Kind: "telemetry", Type: stream, Stream: stream,
		OrganizationID: org, ApplicationID: "app-1", Application: "collector", DeviceID: "dev-1",
		Environment: "production", Time: ts, ReceivedAt: ts.Add(time.Second), Data: json.RawMessage(data)}
}

func TestEventsIdempotentOrderedAndIsolated(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			org, other := "org-"+uuid.NewString(), "org-"+uuid.NewString()
			base := time.Date(2026, 11, 11, 10, 0, 0, 0, time.UTC)
			// Arrives out of order: stored by source time.
			batch := []*envelope.Event{
				event(org, "e3", "energy.power", base.Add(3*time.Minute), `{"power":30,"site":"a"}`),
				event(org, "e1", "energy.power", base.Add(1*time.Minute), `{"power":10,"site":"a"}`),
				event(org, "e2", "energy.power", base.Add(2*time.Minute), `{"power":20,"site":"b"}`),
				event(org, "e1", "energy.power", base.Add(1*time.Minute), `{"power":999}`),  // duplicate in batch
				event(other, "e1", "energy.power", base.Add(1*time.Minute), `{"power":77}`), // same id, other org
				event(org, "v1", "energy.voltage", base, `{"voltage":230}`),
			}
			inserted, err := s.InsertEvents(ctx, batch)
			if err != nil {
				t.Fatal(err)
			}
			want := []bool{true, true, true, false, true, true}
			if fmt.Sprint(inserted) != fmt.Sprint(want) {
				t.Fatalf("inserted=%v want %v", inserted, want)
			}
			again, err := s.InsertEvents(ctx, batch[:3])
			if err != nil || fmt.Sprint(again) != "[false false false]" {
				t.Fatalf("redelivery must be a no-op: %v %v", again, err)
			}

			q := Query{OrganizationID: org, Stream: "energy.power", Limit: 2}
			page1, next, err := s.QueryEvents(ctx, q)
			if err != nil {
				t.Fatal(err)
			}
			if len(page1) != 2 || page1[0].ID != "e1" || page1[1].ID != "e2" || next == nil {
				t.Fatalf("page1 wrong: %+v next=%v", page1, next)
			}
			var first map[string]any
			json.Unmarshal(page1[0].Data, &first)
			if first["power"].(float64) != 10 {
				t.Fatalf("duplicate overwrote the first write: %v", first)
			}
			q.After = next
			page2, next2, _ := s.QueryEvents(ctx, q)
			if len(page2) != 1 || page2[0].ID != "e3" || next2 != nil {
				t.Fatalf("page2 wrong: %+v", page2)
			}

			desc, _, _ := s.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10, Descending: true})
			if len(desc) != 3 || desc[0].ID != "e3" {
				t.Fatalf("descending wrong: %+v", desc)
			}
			ranged, _, _ := s.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10,
				Start: base.Add(2 * time.Minute), End: base.Add(3 * time.Minute)})
			if len(ranged) != 1 || ranged[0].ID != "e2" {
				t.Fatalf("range is [start, end): %+v", ranged)
			}
			filtered, _, _ := s.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10,
				Filter: map[string]json.RawMessage{"site": json.RawMessage(`"b"`)}})
			if len(filtered) != 1 || filtered[0].ID != "e2" {
				t.Fatalf("filter wrong: %+v", filtered)
			}
			none, _, _ := s.QueryEvents(ctx, Query{OrganizationID: org, Stream: "energy.power", Limit: 10, DeviceID: "dev-9"})
			if len(none) != 0 {
				t.Fatal("device filter")
			}
			iso, _, _ := s.QueryEvents(ctx, Query{OrganizationID: other, Stream: "energy.power", Limit: 10})
			if len(iso) != 1 || iso[0].OrganizationID != other {
				t.Fatalf("organization isolation broken: %+v", iso)
			}
		})
	}
}

func TestAggregate(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			org := "org-" + uuid.NewString()
			base := time.Date(2026, 11, 11, 10, 0, 0, 0, time.UTC)
			var batch []*envelope.Event
			for i := 0; i < 6; i++ {
				batch = append(batch, event(org, fmt.Sprintf("a%d", i), "energy.power",
					base.Add(time.Duration(i)*20*time.Second), fmt.Sprintf(`{"power":%d,"nested":{"v":%d}}`, i*10, i)))
			}
			batch = append(batch, event(org, "text", "energy.power", base.Add(10*time.Second), `{"power":"n/a"}`))
			if _, err := s.InsertEvents(ctx, batch); err != nil {
				t.Fatal(err)
			}
			q := AggregateQuery{Query: Query{OrganizationID: org, Stream: "energy.power"}, Field: []string{"power"},
				Func: "avg", Bucket: time.Minute}
			got, err := s.Aggregate(ctx, q)
			if err != nil {
				t.Fatal(err)
			}
			if len(got) != 2 || *got[0].Value != 10 || got[0].Count != 3 || *got[1].Value != 40 || !got[0].Start.Equal(base) {
				t.Fatalf("avg wrong: %+v", got)
			}
			q.Func = "max"
			q.Field = []string{"nested", "v"}
			got, _ = s.Aggregate(ctx, q)
			if *got[0].Value != 2 || *got[1].Value != 5 {
				t.Fatalf("max wrong: %+v", got)
			}
			q.Func = "count"
			got, _ = s.Aggregate(ctx, q)
			if got[0].Count != 4 || got[1].Count != 3 {
				t.Fatalf("count counts rows: %+v", got)
			}
			q.Func = "sum"
			q.Field = []string{"missing"}
			got, _ = s.Aggregate(ctx, q)
			if got[0].Value != nil || got[0].Count != 0 {
				t.Fatalf("missing field must give null: %+v", got[0])
			}
		})
	}
}

func TestCommandLifecycle(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			org := "org-" + uuid.NewString()
			now := time.Now().UTC().Truncate(time.Millisecond)
			c := &Command{ID: uuid.NewString(), OrganizationID: org, ApplicationID: "app-1", Application: "collector",
				Name: "restart", Payload: json.RawMessage(`{"delay":1}`), State: StatePending, IssuedBy: "user:1",
				IdempotencyKey: "k1", MaxAttempts: 3, TimeoutSeconds: 30, CreatedAt: now, UpdatedAt: now,
				ExpiresAt: now.Add(time.Hour), NextCheckAt: now}
			created, ok, err := s.CreateCommand(ctx, c)
			if err != nil || !ok || created.State != StatePending {
				t.Fatalf("create: %v %v", ok, err)
			}
			dup := *c
			dup.ID = uuid.NewString()
			existing, ok, err := s.CreateCommand(ctx, &dup)
			if err != nil || ok || existing.ID != c.ID {
				t.Fatalf("idempotency key must return the first command: %v %v %+v", ok, err, existing)
			}
			// The key belongs to its sender: another sender with the same key gets its own command.
			other := *c
			other.ID, other.IssuedBy, other.State, other.NextCheckAt = uuid.NewString(), "app:other", StateSucceeded, now.Add(time.Hour)
			if got, ok, err := s.CreateCommand(ctx, &other); err != nil || !ok || got.ID != other.ID {
				t.Fatalf("another sender must not receive or suppress this command: %v %v", ok, err)
			}
			if _, err := s.GetCommand(ctx, "other-org", c.ID); err != ErrNotFound {
				t.Fatalf("organization isolation: %v", err)
			}

			due, err := s.ClaimDueCommands(ctx, now.Add(time.Second), time.Minute, 10)
			if err != nil || len(due) != 1 {
				t.Fatalf("claim: %v %d", err, len(due))
			}
			if again, _ := s.ClaimDueCommands(ctx, now.Add(time.Second), time.Minute, 10); len(again) != 0 {
				t.Fatal("a claimed command must be leased")
			}
			pending, _ := s.UndeliveredCommands(ctx, org, "app-1", "dev-1", 10)
			if len(pending) != 1 {
				t.Fatal("undelivered should list the pending command")
			}

			at := now.Add(time.Second)
			upd, changed, err := s.UpdateCommand(ctx, org, c.ID, CommandUpdate{From: []string{StatePending, StateDispatched},
				State: StateDispatched, IncAttempts: true, DispatchedAt: &at, NextCheckAt: &at})
			if err != nil || !changed || upd.State != StateDispatched || upd.Attempts != 1 {
				t.Fatalf("dispatch: %+v %v %v", upd, changed, err)
			}
			done := now.Add(2 * time.Second)
			upd, changed, _ = s.UpdateCommand(ctx, org, c.ID, CommandUpdate{From: []string{StateDispatched},
				State: StateSucceeded, CompletedAt: &done, Result: json.RawMessage(`{"ok":true}`)})
			if !changed || upd.State != StateSucceeded || string(upd.Result) == "" {
				t.Fatalf("succeed: %+v", upd)
			}
			// A late transition from a state the command is no longer in changes nothing.
			msg := "late"
			upd, changed, err = s.UpdateCommand(ctx, org, c.ID, CommandUpdate{From: []string{StateDispatched},
				State: StateFailed, Error: &msg})
			if err != nil || changed || upd.State != StateSucceeded {
				t.Fatalf("terminal state must be final: %+v %v %v", upd, changed, err)
			}
			if pending, _ := s.UndeliveredCommands(ctx, org, "app-1", "dev-1", 10); len(pending) != 0 {
				t.Fatal("finished command still listed as undelivered")
			}
		})
	}
}
