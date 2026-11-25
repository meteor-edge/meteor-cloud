package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

func TestSpelledOutDefaultKeepsTheAlert(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", hotDef))
	h.send("dev-a", 0, `{"t":90}`)
	h.update(rule("r", "org-1", hotDef[:len(hotDef)-1]+`,"alerting":{"mode":"single"}}`))
	h.expect(tr{"dev-a", "firing", ""})
}

func TestAbsenceStreamIsRead(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", `{"schema_version":1,"name":"a","scope":{"type":"device","device_ids":["dev-a"]},
		"condition":{"type":"all","conditions":[
			{"type":"absence","duration":"5m","streams":["meteor.device.status"]},
			{"type":"consecutive","count":2,"condition":{"type":"comparison","field":"t","operator":"gt","value":80}}]}}`))
	status := func(id string, at time.Duration) {
		h.clock.Set(t0.Add(at))
		if err := h.e.Handle(context.Background(), &eval.Event{ID: id, OrgID: "org-1", DeviceID: "dev-a",
			Stream: "meteor.device.status", Time: t0.Add(at), Fields: map[string]eval.Value{"t": eval.Num(1)}}); err != nil {
			t.Fatal(err)
		}
	}
	h.send("dev-a", 0, `{"t":90}`)
	status("s1", time.Minute)
	h.send("dev-a", 2*time.Minute, `{"t":90}`) // the status event neither broke nor fed the streak
	h.advance(5*time.Minute + 30*time.Second)
	h.expect() // status was heard 4.5 minutes ago
	h.advance(6 * time.Minute)
	h.expect(tr{"dev-a", "firing", ""})
	status("s2", 7*time.Minute)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonCleared})
}

// conflicting makes the next n commits that write a state lose to another worker.
type conflicting struct {
	*state.Memory
	n int
}

func (c *conflicting) CommitMany(ctx context.Context, bs []*state.Batch) []error {
	if c.n > 0 && len(bs[0].States) > 0 {
		c.n--
		s := bs[0].States[0]
		cur, _ := c.LoadStates(ctx, s.DeviceID, []string{s.RuleID})
		c.Memory.CommitMany(ctx, []*state.Batch{{States: []*state.RuleState{cur[s.RuleID]}}})
	}
	return c.Memory.CommitMany(ctx, bs)
}

func TestReconcileRetriesConflicts(t *testing.T) {
	for _, conflicts := range []int{1, 100} {
		h := newHarness(t, rule("r", "org-1", hotDef))
		h.send("dev-a", 0, `{"t":90}`)
		h.e.Close()
		e := New(Config{CommitLinger: time.Millisecond}, &conflicting{h.store, conflicts}, h.clock, nil, nil)
		rs := CompileSnapshot(&Snapshot{Version: "empty", Devices: testDevices}, dsl.DefaultLimits())
		e.Apply(rs)
		err := e.Reconcile(context.Background(), rs)
		e.Close()
		h.e = h.newEngine()
		if conflicts == 1 {
			if err != nil {
				t.Fatal(err)
			}
			h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonRemoved})
		} else if !errors.Is(err, state.ErrConflict) {
			t.Fatalf("exhausted retries must be reported, got %v", err)
		}
	}
}

func TestFailedCommitIsNotCountedAsFiring(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", hotDef))
	h.store.FailCommits = 1
	if h.handle("e1", "dev-a", 0, `{"t":90}`) == nil || h.e.counted.len() != 0 {
		t.Fatalf("firing %d after a failed commit", h.e.counted.len())
	}
	h.sendID("e1", "dev-a", 0, `{"t":90}`)
	if n := h.e.counted.len(); n != 1 {
		t.Fatalf("firing %d", n)
	}
	h.update()
	if n := h.e.counted.len(); n != 0 {
		t.Fatalf("firing %d after the rule was removed", n)
	}
}

// slow blocks its first commit until released.
type slow struct {
	*state.Memory
	entered, release chan struct{}
}

func (s *slow) CommitMany(ctx context.Context, bs []*state.Batch) []error {
	s.entered <- struct{}{}
	<-s.release
	return s.Memory.CommitMany(ctx, bs)
}

func TestStoppedCommitterReleasesItsCallers(t *testing.T) {
	for range 20 { // stop and the queue race: either may win
		s := &slow{state.NewMemory(), make(chan struct{}, 100), make(chan struct{})}
		c := newCommitter(s, NewMetrics(nil), 1, time.Microsecond)
		errs := make(chan error, 5)
		for i := range 5 {
			go func() {
				errs <- c.commit(context.Background(), &state.Batch{TimersClear: []state.Key{{RuleID: fmt.Sprint(i)}}})
			}()
		}
		var once sync.Once
		release := func() { once.Do(func() { close(s.release) }) }
		t.Cleanup(release)
		setup := time.After(5 * time.Second)
		select {
		case <-s.entered:
		case <-setup:
			t.Fatal("no commit started")
		}
		for len(c.reqs) < 4 {
			select {
			case <-setup:
				t.Fatalf("only %d callers queued", len(c.reqs))
			case <-time.After(time.Millisecond):
			}
		}
		time.AfterFunc(time.Millisecond, release)
		c.close()
		for range 5 {
			select {
			case <-errs:
			case <-time.After(5 * time.Second):
				t.Fatal("a caller is still waiting after close")
			}
		}
	}
}

func TestLabelSelectorNeedsTheLabel(t *testing.T) {
	labels := map[string]string{"zone": ""}
	rs := CompileSnapshot(&Snapshot{Devices: []Device{{ID: "with", OrganizationID: "o", Labels: labels}, {ID: "without", OrganizationID: "o"}},
		Rules: []RuleRecord{rule("r", "o", `{"schema_version":1,"name":"x","scope":{"type":"organization","labels":{"zone":""}},"condition":{"type":"absence","duration":"5m"}}`)}}, dsl.DefaultLimits())
	labels["zone"] = "changed later"
	r, _ := rs.Rule("r")
	if ds := rs.MatchingDevices(r); len(ds) != 1 || ds[0].ID != "with" {
		t.Fatalf("matched %v", ds)
	}
}

func TestSimulatedIDsDoNotCollide(t *testing.T) {
	res, err := Simulate(context.Background(), SimRequest{Definition: json.RawMessage(hotDef), Events: []SimEvent{
		{ID: "event-2", Time: t0, Data: json.RawMessage(`{"t":1}`)},
		{Time: t0.Add(time.Second), Data: json.RawMessage(`{"t":90}`)},
	}}, dsl.DefaultLimits(), false)
	if err != nil || len(res.Steps) != 2 || len(res.Steps[1].Transitions) != 1 {
		t.Fatalf("%v %+v", err, res)
	}
}
