package engine

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
)

func TestOlderSnapshotCannotOverwriteANewerVersion(t *testing.T) {
	v1 := rule("r", "org-1", hotDef)
	v2 := rule("r", "org-1", `{"schema_version":1,"name":"hot","scope":{"type":"organization"},"condition":{"type":"comparison","field":"t","operator":"gt","value":70}}`)
	v1.Version, v2.Version = 1, 2
	h := newHarness(t, v2)
	h.send("dev-a", 0, `{"t":75}`)
	old := CompileSnapshot(&Snapshot{Version: "old", Rules: []RuleRecord{v1}, Devices: testDevices}, dsl.DefaultLimits())
	h.e.Apply(old)
	if err := h.handle("late", "dev-a", time.Second, `{"t":75}`); !errors.Is(err, ErrStale) {
		t.Fatalf("stale worker: %v", err)
	}
	if err := h.e.Reconcile(context.Background(), old); err != nil {
		t.Fatal(err)
	}
	h.expect(tr{"dev-a", "firing", ""})
}

func TestTickDoesNotMoveBehindTheWatermark(t *testing.T) {
	// The device clock is two minutes ahead; a timer runs at the wall clock.
	h := newHarness(t, rule("r", "org-1", `{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"aggregate","function":"max","field":"t","window":"1m","operator":"gt","value":80}}`))
	now := h.clock.Now()
	h.send("dev-a", 2*time.Minute, `{"t":90}`)
	h.clock.Set(now)
	for _, tm := range h.store.Timers() {
		if err := h.e.Tick(context.Background(), tm); err != nil {
			t.Fatal(err)
		}
	}
	h.expect(tr{"dev-a", "firing", ""})
}

func TestResolveSeenOnlyAfterARestartKeepsTheGaugeAtZero(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", hotDef))
	h.send("dev-a", 0, `{"t":90}`)
	h.restart()
	h.send("dev-a", time.Minute, `{"t":10}`)
	if n := h.e.counted.len(); n != 0 {
		t.Fatal(n)
	}
}

func TestSimulationShowsWebhookOnlyTransitionsOnce(t *testing.T) {
	def := hotDef[:len(hotDef)-1] + `,"actions":[{"type":"webhook","integration_id":"a"},{"type":"webhook","integration_id":"b"}]}`
	res, err := Simulate(context.Background(), SimRequest{Definition: json.RawMessage(def), Events: []SimEvent{
		{Time: time.Unix(0, 0), Data: json.RawMessage(`{"t":90}`)}}}, dsl.DefaultLimits(), false)
	if err != nil || len(res.Steps) != 1 || len(res.Steps[0].Transitions) != 1 {
		t.Fatal(err, res)
	}
}

func TestCloseTwice(t *testing.T) {
	h := newHarness(t)
	done := make(chan struct{})
	go func() { h.e.Close(); close(done) }()
	h.e.Close()
	<-done
}
