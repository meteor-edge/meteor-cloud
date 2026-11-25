package state

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"meteorcloud/dataplane/internal/rules/eval"
)

// Stores under test: always the memory store, and PostgreSQL when
// RULES_TEST_DATABASE_URL is set (CI sets it).
func stores(t *testing.T) map[string]Store {
	t.Helper()
	out := map[string]Store{"memory": NewMemory()}
	if url := os.Getenv("RULES_TEST_DATABASE_URL"); url != "" {
		ctx := context.Background()
		// Test packages run in parallel and share the database: serialize on a lock.
		lock, err := pgx.Connect(ctx, url)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := lock.Exec(ctx, `SELECT pg_advisory_lock(77880042)`); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { lock.Close(context.Background()) })
		p, err := OpenPostgres(ctx, url, 8)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := p.pool.Exec(ctx, `TRUNCATE rules.rule_state, rules.device_cursor, rules.rule_timers, rules.action_outbox`); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(p.Close)
		out["postgres"] = p
	}
	return out
}

var t0 = time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)

func newState(rule, dev string) *RuleState {
	b := true
	return &RuleState{RuleID: rule, DeviceID: dev, OrgID: "org", RuleVersion: 1, ConditionHash: "h", Phase: PhaseNormal,
		ActivatedAt: t0, Nodes: eval.State{"c": {Last: &b}}}
}

func commit(t *testing.T, s Store, b *Batch) error {
	t.Helper()
	return s.CommitMany(context.Background(), []*Batch{b})[0]
}

func TestStoreContract(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			st := newState("r1", "d1")
			cur := &Cursor{DeviceID: "d1", OrgID: "org", Watermark: t0}
			cur.Remember("e1")
			if err := commit(t, s, &Batch{Cursor: cur, States: []*RuleState{st},
				TimersSet: []Timer{{RuleID: "r1", DeviceID: "d1", OrgID: "org", Due: t0.Add(time.Minute)}},
				Outbox:    []OutboxItem{{Key: "k1", OrgID: "org", RuleID: "r1", DeviceID: "d1", Transition: "firing", ActionType: "alert", Payload: []byte(`{}`), NextAttempt: t0, CreatedAt: t0}}}); err != nil {
				t.Fatal(err)
			}
			if st.Version != 1 || cur.Version != 1 {
				t.Fatalf("versions advanced: %d %d", st.Version, cur.Version)
			}
			got, err := s.LoadStates(ctx, "d1", []string{"r1", "r2"})
			if err != nil || len(got) != 1 || got["r1"].Nodes["c"].Last == nil || !*got["r1"].Nodes["c"].Last || got["r1"].Version != 1 {
				t.Fatalf("load: %v %+v", err, got)
			}
			c, _ := s.LoadCursor(ctx, "d1")
			if c == nil || !c.Seen("e1") || !c.Watermark.Equal(t0) {
				t.Fatalf("cursor %+v", c)
			}
			if c, _ := s.LoadCursor(ctx, "nobody"); c != nil {
				t.Fatal("unknown cursor is nil")
			}

			// A stale writer loses.
			stale := got["r1"].Clone()
			stale.Version = 0
			if err := commit(t, s, &Batch{States: []*RuleState{stale}}); !errors.Is(err, ErrConflict) {
				t.Fatalf("insert over existing: %v", err)
			}
			fresh := got["r1"]
			fresh.Phase = PhaseFiring
			if err := commit(t, s, &Batch{States: []*RuleState{fresh}}); err != nil {
				t.Fatal(err)
			}
			old := got["r1"].Clone()
			old.Version = 1
			if err := commit(t, s, &Batch{States: []*RuleState{old}}); !errors.Is(err, ErrConflict) {
				t.Fatalf("stale update: %v", err)
			}

			// Group commit: one conflicting batch does not undo its neighbours.
			a, b := newState("r2", "d1"), newState("r3", "d1")
			conflicting := got["r1"].Clone()
			conflicting.Version = 1
			errs := s.CommitMany(ctx, []*Batch{{States: []*RuleState{a}}, {States: []*RuleState{conflicting}}, {States: []*RuleState{b}}})
			if errs[0] != nil || !errors.Is(errs[1], ErrConflict) || errs[2] != nil {
				t.Fatalf("group commit: %v", errs)
			}
			if all, _ := s.LoadStates(ctx, "d1", []string{"r1", "r2", "r3"}); len(all) != 3 || all["r1"].Phase != PhaseFiring {
				t.Fatalf("after group commit: %+v", all)
			}

			// The outbox is idempotent by key.
			dup := OutboxItem{Key: "k1", OrgID: "org", RuleID: "r1", DeviceID: "d1", Transition: "firing", ActionType: "alert", Payload: []byte(`{}`), NextAttempt: t0, CreatedAt: t0}
			if err := commit(t, s, &Batch{Outbox: []OutboxItem{dup}}); err != nil {
				t.Fatal(err)
			}
			if _, outbox, _ := s.Counts(ctx); outbox != 1 {
				t.Fatalf("outbox %d", outbox)
			}

			// Timers: claimed with a lease, invisible until it ends.
			if ts, _ := s.ClaimTimers(ctx, t0, time.Minute, 10); len(ts) != 0 {
				t.Fatal("not due yet")
			}
			ts, err := s.ClaimTimers(ctx, t0.Add(time.Minute), 30*time.Second, 10)
			if err != nil || len(ts) != 1 || !ts[0].Due.Equal(t0.Add(time.Minute)) {
				t.Fatalf("claim: %v %+v", err, ts)
			}
			if ts, _ := s.ClaimTimers(ctx, t0.Add(80*time.Second), 30*time.Second, 10); len(ts) != 0 {
				t.Fatal("leased")
			}
			if ts, _ := s.ClaimTimers(ctx, t0.Add(91*time.Second), 30*time.Second, 10); len(ts) != 1 {
				t.Fatal("lease expired: claimable again")
			}

			// Paging and deletion.
			page, _ := s.StatesForRule(ctx, "r1", "", 10)
			if len(page) != 1 {
				t.Fatalf("page %d", len(page))
			}
			if err := commit(t, s, &Batch{Deleted: []*RuleState{page[0]}}); err != nil {
				t.Fatal(err)
			}
			if timers, _, _ := s.Counts(ctx); timers != 0 {
				t.Fatalf("timer removed with its state, %d left", timers)
			}
			if err := s.Ping(ctx); err != nil {
				t.Fatal(err)
			}
		})
	}
}
