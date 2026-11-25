package engine

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

// End-to-end engine behaviour on the real store: several workers, one device, one
// alert; then a restart in the middle of a silence. Runs when RULES_TEST_DATABASE_URL
// is set.
func TestEngineOnPostgres(t *testing.T) {
	url := os.Getenv("RULES_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("RULES_TEST_DATABASE_URL not set")
	}
	ctx := context.Background()
	// Test packages run in parallel and share the database: serialize on a lock.
	lock, err := pgx.Connect(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close(context.Background())
	if _, err := lock.Exec(ctx, `SELECT pg_advisory_lock(77880042)`); err != nil {
		t.Fatal(err)
	}
	open := func() *state.Postgres {
		p, err := state.OpenPostgres(ctx, url, 16)
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	admin := open()
	defer admin.Close()
	snap := &Snapshot{Version: "pg", Devices: testDevices, Rules: []RuleRecord{
		rule("cpu", "org-1", cpuRule),
		rule("silent", "org-1", `{"schema_version":1,"name":"Silent","scope":{"type":"device","device_ids":["dev-b"]},"condition":{"type":"absence","duration":"10m"}}`),
	}}
	clock := NewFakeClock(t0.Add(time.Minute))
	start := func(store state.Store) *Engine {
		e := New(Config{CommitLinger: 2 * time.Millisecond}, store, clock, nil, nil)
		e.Apply(CompileSnapshot(snap, dsl.DefaultLimits()))
		return e
	}
	reset := func() {
		for _, q := range []string{"rule_state", "device_cursor", "rule_timers", "action_outbox"} {
			if err := execAdmin(ctx, url, "TRUNCATE rules."+q); err != nil {
				t.Fatal(err)
			}
		}
	}
	reset()

	t.Run("concurrent workers create one alert", func(t *testing.T) {
		var workers []*Engine
		for i := 0; i < 4; i++ {
			p := open()
			defer p.Close()
			w := start(p)
			defer w.Close()
			workers = append(workers, w)
		}
		fields, _ := eval.FlattenJSON([]byte(`{"cpu":99}`))
		var wg sync.WaitGroup
		for i := 0; i < 100; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				ev := &eval.Event{ID: fmt.Sprintf("p%d", i), OrgID: "org-1", DeviceID: "dev-a", Stream: dsl.DefaultStream,
					Time: t0.Add(time.Duration(i) * time.Millisecond), Fields: fields}
				for {
					err := workers[i%4].Handle(ctx, ev)
					if errors.Is(err, state.ErrConflict) {
						continue
					}
					if err != nil {
						t.Error(err)
					}
					return
				}
			}(i)
		}
		wg.Wait()
		items, err := admin.Outbox(ctx, 100)
		if err != nil {
			t.Fatal(err)
		}
		if len(items) != 1 || items[0].Transition != "firing" || items[0].DeviceID != "dev-a" {
			t.Fatalf("one firing alert, got %d: %+v", len(items), items)
		}
	})

	t.Run("silence timer survives restart", func(t *testing.T) {
		p := open()
		e := start(p)
		fields, _ := eval.FlattenJSON([]byte(`{"cpu":1}`))
		if err := e.Handle(ctx, &eval.Event{ID: "b1", OrgID: "org-1", DeviceID: "dev-b", Stream: dsl.DefaultStream, Time: t0.Add(time.Minute), Fields: fields}); err != nil {
			t.Fatal(err)
		}
		e.Close()
		p.Close() // the worker is gone

		p2 := open()
		defer p2.Close()
		e2 := start(p2)
		defer e2.Close()
		clock.Set(t0.Add(11 * time.Minute))
		if n, err := e2.FireDue(ctx); err != nil || n != 1 {
			t.Fatalf("fired %d: %v", n, err)
		}
		items, _ := admin.Outbox(ctx, 100)
		found := false
		for _, it := range items {
			if it.RuleID == "silent" && it.Transition == "firing" && it.DeviceID == "dev-b" {
				found = true
			}
		}
		if !found {
			t.Fatalf("silence alert after restart: %+v", items)
		}
	})
}

func execAdmin(ctx context.Context, url, sql string) error {
	conn, err := pgx.Connect(ctx, url)
	if err != nil {
		return err
	}
	defer conn.Close(ctx)
	_, err = conn.Exec(ctx, sql)
	return err
}
