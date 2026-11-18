package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

var t0 = time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)

type harness struct {
	t     *testing.T
	store *state.Memory
	clock *FakeClock
	e     *Engine
	snap  *Snapshot
	seq   int
}

var testDevices = []Device{
	{ID: "dev-a", OrganizationID: "org-1", GroupID: "grp-1", DeviceTypeID: "rpi", Labels: map[string]string{"env": "production"}},
	{ID: "dev-b", OrganizationID: "org-1", GroupID: "grp-2", DeviceTypeID: "rpi", Labels: map[string]string{"env": "staging"}},
	{ID: "dev-x", OrganizationID: "org-2", DeviceTypeID: "rpi"},
}

func newHarness(t *testing.T, rules ...RuleRecord) *harness {
	t.Helper()
	h := &harness{t: t, store: state.NewMemory(), clock: NewFakeClock(t0)}
	h.snap = &Snapshot{Version: "v1", Rules: rules, Devices: testDevices}
	h.e = h.newEngine()
	t.Cleanup(func() { h.e.Close() })
	return h
}

func (h *harness) newEngine() *Engine {
	e := New(Config{CommitLinger: time.Millisecond, CommitBatch: 64}, h.store, h.clock, nil, nil)
	rs := CompileSnapshot(h.snap, dsl.DefaultLimits())
	if len(rs.Errors) > 0 {
		h.t.Fatalf("snapshot errors: %v", rs.Errors)
	}
	e.Apply(rs)
	return e
}

// restart drops the engine (and all its memory) and starts a new one on the same store.
func (h *harness) restart() {
	h.e.Close()
	h.e = h.newEngine()
}

func (h *harness) update(rules ...RuleRecord) {
	h.snap = &Snapshot{Version: fmt.Sprintf("v%d", len(rules)+h.seq), Rules: rules, Devices: testDevices}
	rs := CompileSnapshot(h.snap, dsl.DefaultLimits())
	old := h.e.Apply(rs)
	if err := h.e.Reconcile(context.Background(), old, rs); err != nil {
		h.t.Fatal(err)
	}
}

func rule(id, org, def string) RuleRecord {
	return RuleRecord{ID: id, OrganizationID: org, Version: 1, Definition: json.RawMessage(def)}
}

func (h *harness) send(device string, at time.Duration, data string) {
	h.t.Helper()
	h.seq++
	h.sendID(fmt.Sprintf("e%d", h.seq), device, at, data)
}

func (h *harness) sendID(id, device string, at time.Duration, data string) {
	h.t.Helper()
	if err := h.handle(id, device, at, data); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) handle(id, device string, at time.Duration, data string) error {
	org := "org-1"
	if device == "dev-x" {
		org = "org-2"
	}
	fields, err := eval.FlattenJSON([]byte(data))
	if err != nil {
		h.t.Fatal(err)
	}
	if t := t0.Add(at); t.After(h.clock.Now()) {
		h.clock.Set(t)
	}
	return h.e.Handle(context.Background(), &eval.Event{ID: id, OrgID: org, DeviceID: device, Kind: "telemetry",
		Stream: dsl.DefaultStream, Time: t0.Add(at), Fields: fields})
}

// advance moves the clock and fires due timers, like the scheduler would.
func (h *harness) advance(to time.Duration) {
	h.t.Helper()
	h.clock.Set(t0.Add(to))
	if _, err := h.e.FireDue(context.Background()); err != nil {
		h.t.Fatal(err)
	}
}

type tr struct{ device, kind, reason string }

func (h *harness) transitions(actionType string) []tr {
	var out []tr
	for _, o := range h.store.Outbox() {
		if o.ActionType != actionType {
			continue
		}
		var p ActionPayload
		_ = json.Unmarshal(o.Payload, &p)
		out = append(out, tr{p.DeviceID, p.Transition.Kind, p.Transition.Reason})
	}
	return out
}

func (h *harness) expect(want ...tr) {
	h.t.Helper()
	got := h.transitions(dsl.ActionAlert)
	if fmt.Sprint(got) != fmt.Sprint(want) {
		h.t.Fatalf("alert transitions:\n got %v\nwant %v", got, want)
	}
}

func (h *harness) phase(ruleID, device string) string {
	st, _ := h.store.LoadStates(context.Background(), device, []string{ruleID})
	if s := st[ruleID]; s != nil {
		return s.Phase
	}
	return "none"
}

const cpuRule = `{"schema_version":1,"name":"High CPU","severity":"critical","scope":{"type":"organization"},
	"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`

func TestThresholdFiresOnceAndResolves(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	h.send("dev-a", 0, `{"cpu":50}`)
	for i := 1; i <= 10; i++ {
		h.send("dev-a", time.Duration(i)*time.Second, `{"cpu":95}`)
	}
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", 20*time.Second, `{"cpu":40}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
	h.send("dev-a", 30*time.Second, `{"cpu":99}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"}, tr{"dev-a", "firing", ""})

	var p ActionPayload
	_ = json.Unmarshal(h.store.Outbox()[0].Payload, &p)
	if p.Severity != "critical" || p.Transition.Values["cpu"] != 95 || p.Transition.EventID == "" || p.Transition.Episode != 1 {
		t.Fatalf("payload: %+v", p)
	}
}

func TestSustainedMemoryPendingFiringViaTimer(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
		"condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`))
	h.send("dev-a", 0, `{"mem":95}`)
	if h.phase("r1", "dev-a") != state.PhasePending {
		t.Fatal("pending")
	}
	h.advance(4 * time.Minute)
	h.expect()
	h.advance(5 * time.Minute) // the device said nothing new: the timer fires it
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", 6*time.Minute, `{"mem":20}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
}

func TestPendingCancelledWhenConditionClears(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
		"condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`))
	h.send("dev-a", 0, `{"mem":95}`)
	h.send("dev-a", 2*time.Minute, `{"mem":10}`)
	h.advance(6 * time.Minute)
	h.expect()
	if h.phase("r1", "dev-a") != state.PhaseNormal {
		t.Fatal("back to normal")
	}
}

func TestMissingTelemetrySurvivesRestart(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Silent","scope":{"type":"device","device_ids":["dev-a"]},
		"condition":{"type":"absence","duration":"10m"}}`))
	h.send("dev-a", 0, `{"cpu":1}`)
	h.send("dev-a", 3*time.Minute, `{"cpu":1}`)
	h.restart() // nothing in memory survives; the timer is a stored row
	h.advance(12 * time.Minute)
	h.expect()
	h.advance(13 * time.Minute)
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", 14*time.Minute, `{"cpu":1}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
	if n := len(h.store.Timers()); n != 1 {
		t.Fatalf("one timer per rule and device, got %d", n)
	}
}

func TestAbsenceTimerIsNotRewrittenOnEveryEvent(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Silent","scope":{"type":"organization"},
		"condition":{"type":"absence","duration":"10m"}}`))
	h.send("dev-a", 0, `{"cpu":1}`)
	first := h.store.Timers()[0].Due
	for i := 1; i < 20; i++ {
		h.send("dev-a", time.Duration(i)*time.Second, `{"cpu":1}`)
	}
	if got := h.store.Timers()[0].Due; !got.Equal(first) {
		t.Fatalf("timer moved on every event: %v -> %v", first, got)
	}
	h.advance(10 * time.Minute) // fires early, sees recent traffic, reschedules
	h.expect()
	if got := h.store.Timers()[0].Due; !got.Equal(t0.Add(19*time.Second + 10*time.Minute)) {
		t.Fatalf("rescheduled to last seen plus 10m, got %v", got)
	}
}

func TestDeviceThatNeverReportsIsSeeded(t *testing.T) {
	h := newHarness(t)
	h.update(rule("r1", "org-1", `{"schema_version":1,"name":"Silent","scope":{"type":"group","group_id":"grp-1"},
		"condition":{"type":"absence","duration":"10m"}}`))
	h.advance(10 * time.Minute)
	h.expect(tr{"dev-a", "firing", ""}) // dev-b is in another group, dev-x another org
}

func TestHysteresisAndCooldown(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Heat","cooldown":"10m","scope":{"type":"organization"},
		"condition":{"type":"comparison","field":"t","operator":"gt","value":80},
		"resolve":{"type":"comparison","field":"t","operator":"lt","value":70}}`))
	h.send("dev-a", 0, `{"t":85}`)
	h.send("dev-a", time.Minute, `{"t":75}`) // below 80 but not below 70: still firing
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", 2*time.Minute, `{"t":65}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
	h.send("dev-a", 3*time.Minute, `{"t":90}`) // cooldown: no new alert
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
	h.advance(12 * time.Minute) // cooldown over, still hot: fires again on the timer
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"}, tr{"dev-a", "firing", ""})
}

func TestRepeatedViolationsAndRateOfChange(t *testing.T) {
	h := newHarness(t,
		rule("flap", "org-1", `{"schema_version":1,"name":"Flap","scope":{"type":"organization"},
			"condition":{"type":"count_within","window":"10m","min_count":5,"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}}`),
		rule("rise", "org-1", `{"schema_version":1,"name":"Rise","scope":{"type":"organization"},
			"condition":{"type":"aggregate","function":"delta","field":"temp","window":"60s","operator":"gte","value":10}}`))
	for i := 0; i < 5; i++ {
		h.send("dev-a", time.Duration(i)*time.Minute, `{"cpu":95,"temp":40}`)
	}
	h.send("dev-a", 4*time.Minute+50*time.Second, `{"cpu":10,"temp":51}`)
	got := h.transitions(dsl.ActionAlert)
	if len(got) != 2 {
		t.Fatalf("flap and rise fire: %v", got)
	}
}

func TestDevicesAreIndependent(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
		"condition":{"type":"duration","duration":"1m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`))
	h.send("dev-a", 0, `{"mem":95}`)
	h.send("dev-b", 30*time.Second, `{"mem":95}`)
	h.send("dev-a", 61*time.Second, `{"mem":95}`)
	h.send("dev-b", 62*time.Second, `{"mem":10}`)
	h.expect(tr{"dev-a", "firing", ""})
	if h.phase("r1", "dev-b") != state.PhaseNormal {
		t.Fatal("dev-b reset on its own")
	}
}

func TestTenantIsolation(t *testing.T) {
	h := newHarness(t,
		rule("r1", "org-1", cpuRule),
		rule("r2", "org-2", cpuRule))
	h.send("dev-x", 0, `{"cpu":99}`)
	h.expect(tr{"dev-x", "firing", ""})
	if o := h.store.Outbox(); o[0].RuleID != "r2" || o[0].OrgID != "org-2" {
		t.Fatalf("org-2 device only meets org-2 rules: %+v", o[0])
	}
	// An event claiming org-1 for org-2's device is never evaluated.
	fields, _ := eval.FlattenJSON([]byte(`{"cpu":99}`))
	err := h.e.Handle(context.Background(), &eval.Event{ID: "spoof", OrgID: "org-1", DeviceID: "dev-x",
		Stream: dsl.DefaultStream, Time: t0, Fields: fields})
	if err != nil || len(h.store.Outbox()) != 1 {
		t.Fatalf("spoofed event evaluated: err=%v outbox=%d", err, len(h.store.Outbox()))
	}
	if _, err := CompileRule(rule("bad", "org-1", `{"schema_version":1,"name":"x","scope":{"type":"platform"},"condition":{"type":"absence","duration":"1m"}}`), dsl.DefaultLimits()); err == nil {
		t.Fatal("an organization rule cannot be platform-wide")
	}
}

func TestPlatformRuleSeesEveryOrganization(t *testing.T) {
	h := newHarness(t, rule("p1", "", `{"schema_version":1,"name":"Fleet CPU","scope":{"type":"platform"},
		"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`))
	h.send("dev-a", 0, `{"cpu":99}`)
	h.send("dev-x", 0, `{"cpu":99}`)
	if len(h.transitions(dsl.ActionAlert)) != 2 {
		t.Fatal("platform rule applies to both organizations")
	}
}

func TestScopeByGroupTypeAndLabels(t *testing.T) {
	h := newHarness(t,
		rule("grp", "org-1", `{"schema_version":1,"name":"g","scope":{"type":"group","group_id":"grp-2"},"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`),
		rule("lbl", "org-1", `{"schema_version":1,"name":"l","scope":{"type":"device_type","device_type_id":"rpi","labels":{"env":"production"}},"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`))
	h.send("dev-a", 0, `{"cpu":99}`)
	h.send("dev-b", 0, `{"cpu":99}`)
	got := map[string]string{}
	for _, o := range h.store.Outbox() {
		got[o.RuleID] = o.DeviceID
	}
	if got["grp"] != "dev-b" || got["lbl"] != "dev-a" || len(got) != 2 {
		t.Fatalf("scoping: %v", got)
	}
}

func TestDuplicateEventsAreIgnored(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"n","scope":{"type":"organization"},
		"condition":{"type":"aggregate","function":"count","field":"cpu","window":"5m","operator":"gte","value":3}}`))
	h.sendID("same", "dev-a", 0, `{"cpu":1}`)
	h.sendID("same", "dev-a", time.Second, `{"cpu":1}`)
	h.sendID("same", "dev-a", 2*time.Second, `{"cpu":1}`)
	h.sendID("other", "dev-a", 3*time.Second, `{"cpu":1}`)
	h.expect()
	h.sendID("third", "dev-a", 4*time.Second, `{"cpu":1}`)
	h.expect(tr{"dev-a", "firing", ""})
}

func TestOutOfOrderAndLateEvents(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	h.send("dev-a", 2*time.Minute, `{"cpu":99}`)
	h.send("dev-a", time.Minute, `{"cpu":10}`) // older: must not resolve
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", -10*time.Minute, `{"cpu":10}`) // beyond late tolerance: dropped
	h.expect(tr{"dev-a", "firing", ""})
	h.send("dev-a", 3*time.Minute, `{"cpu":10}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "condition_cleared"})
}

func TestConcurrentWorkersCreateOneAlert(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	workers := []*Engine{h.e, h.newEngine(), h.newEngine(), h.newEngine()}
	defer func() {
		for _, w := range workers[1:] {
			w.Close()
		}
	}()
	h.clock.Set(t0.Add(time.Minute))
	fields, _ := eval.FlattenJSON([]byte(`{"cpu":99}`))
	var wg sync.WaitGroup
	errs := make(chan error, 200)
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			w := workers[i%len(workers)]
			ev := &eval.Event{ID: fmt.Sprintf("c%d", i), OrgID: "org-1", DeviceID: "dev-a", Stream: dsl.DefaultStream,
				Time: t0.Add(time.Duration(i) * time.Millisecond), Fields: fields}
			for {
				err := w.Handle(context.Background(), ev)
				if errors.Is(err, state.ErrConflict) {
					continue // the transport would redeliver
				}
				if err != nil {
					errs <- err
				}
				return
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	h.expect(tr{"dev-a", "firing", ""})
}

func TestStateSurvivesRestartMidDuration(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
		"condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`))
	h.send("dev-a", 0, `{"mem":95}`)
	h.send("dev-a", 2*time.Minute, `{"mem":95}`)
	h.restart()
	h.send("dev-a", 5*time.Minute, `{"mem":95}`)
	h.expect(tr{"dev-a", "firing", ""})
}

func TestPersistenceFailureIsRetriedWithoutDuplicates(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	h.store.FailCommits = 1
	if err := h.handle("e1", "dev-a", 0, `{"cpu":99}`); err == nil {
		t.Fatal("commit failure must fail Handle so the event is redelivered")
	}
	h.expect()
	h.sendID("e1", "dev-a", 0, `{"cpu":99}`) // redelivery
	h.sendID("e1", "dev-a", 0, `{"cpu":99}`) // and a duplicate
	h.expect(tr{"dev-a", "firing", ""})
}

func TestRuleUpdatePresentationKeepsStateConditionResets(t *testing.T) {
	mem := `{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
		"condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`
	h := newHarness(t, rule("r1", "org-1", mem))
	h.send("dev-a", 0, `{"mem":95}`)
	renamed := strings.Replace(mem, `"Mem"`, `"Memory high"`, 1)
	h.update(RuleRecord{ID: "r1", OrganizationID: "org-1", Version: 2, Definition: json.RawMessage(renamed)})
	h.send("dev-a", 5*time.Minute, `{"mem":95}`)
	h.expect(tr{"dev-a", "firing", ""}) // the 5 minutes started under version 1 still count

	changed := strings.Replace(renamed, `"value":90`, `"value":99`, 1)
	h.update(RuleRecord{ID: "r1", OrganizationID: "org-1", Version: 3, Definition: json.RawMessage(changed)})
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "rule_updated"})
	st, _ := h.store.LoadStates(context.Background(), "dev-a", []string{"r1"})
	if st["r1"].RuleVersion != 3 || st["r1"].Phase != state.PhaseNormal {
		t.Fatalf("state reset to version 3: %+v", st["r1"])
	}
}

func TestRemovedRuleResolvesItsAlerts(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	h.send("dev-a", 0, `{"cpu":99}`)
	h.update()
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", "rule_removed"})
	st, _ := h.store.LoadStates(context.Background(), "dev-a", []string{"r1"})
	if len(st) != 0 {
		t.Fatal("state deleted")
	}
}

func TestActionsFollowTransitionsWithStableKeys(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"c","scope":{"type":"organization"},
		"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90},
		"actions":[{"type":"alert"},{"type":"webhook","integration_id":"h1"},{"type":"command","command":"reboot","on":["resolved"]}]}`))
	h.send("dev-a", 0, `{"cpu":99}`)
	h.send("dev-a", time.Second, `{"cpu":1}`)
	var kinds []string
	keys := map[string]bool{}
	for _, o := range h.store.Outbox() {
		kinds = append(kinds, o.ActionType+":"+o.Transition)
		if keys[o.Key] {
			t.Fatalf("duplicate key %s", o.Key)
		}
		keys[o.Key] = true
	}
	want := "[alert:firing webhook:firing alert:resolved command:resolved]"
	if fmt.Sprint(kinds) != want {
		t.Fatalf("got %v want %v", kinds, want)
	}
	if !keys["r1:dev-a:1:firing:a0"] {
		t.Fatalf("keys: %v", keys)
	}
}

func TestPerEventModeCapsInstances(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", `{"schema_version":1,"name":"c","scope":{"type":"organization"},"alerting":{"mode":"per_event","max_active":3},
		"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`))
	for i := 0; i < 10; i++ {
		h.send("dev-a", time.Duration(i)*time.Second, `{"cpu":99}`)
	}
	if n := len(h.transitions(dsl.ActionAlert)); n != 3 {
		t.Fatalf("3 instances, got %d", n)
	}
}

func TestUnknownDeviceAndNoRulesAreAcked(t *testing.T) {
	h := newHarness(t, rule("r1", "org-1", cpuRule))
	fields, _ := eval.FlattenJSON([]byte(`{"cpu":99}`))
	if err := h.e.Handle(context.Background(), &eval.Event{ID: "x", OrgID: "org-1", DeviceID: "ghost", Stream: dsl.DefaultStream, Time: t0, Fields: fields}); err != nil {
		t.Fatal(err)
	}
	err := h.e.Handle(context.Background(), &eval.Event{ID: "", OrgID: "org-1", DeviceID: "dev-a", Time: t0})
	if !errors.Is(err, ErrPermanent) {
		t.Fatalf("missing id is permanent: %v", err)
	}
	if err := New(Config{}, state.NewMemory(), nil, nil, nil).Handle(context.Background(), &eval.Event{ID: "x"}); !errors.Is(err, ErrNotReady) {
		t.Fatalf("not ready: %v", err)
	}
}

func TestSnapshotRefusesInvalidRulesButLoadsTheRest(t *testing.T) {
	rs := CompileSnapshot(&Snapshot{Rules: []RuleRecord{rule("ok", "org-1", cpuRule), rule("bad", "org-1", `{"schema_version":1}`)}}, dsl.DefaultLimits())
	if rs.Len() != 1 || len(rs.Errors) != 1 || rs.Errors[0].RuleID != "bad" {
		t.Fatalf("%d rules, errors %v", rs.Len(), rs.Errors)
	}
}

func TestSimulate(t *testing.T) {
	until := t0.Add(20 * time.Minute)
	res, err := Simulate(context.Background(), SimRequest{
		Definition: json.RawMessage(`{"schema_version":1,"name":"Mem","scope":{"type":"organization"},
			"condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}}`),
		Events: []SimEvent{
			{Time: t0, Data: json.RawMessage(`{"mem":95}`)},
			{Time: t0.Add(time.Minute), Data: json.RawMessage(`{"mem":96}`)},
			{Time: t0.Add(10 * time.Minute), Data: json.RawMessage(`{"mem":10}`)},
		},
		Until: &until,
	}, dsl.DefaultLimits(), false)
	if err != nil {
		t.Fatal(err)
	}
	var trace []string
	for _, s := range res.Steps {
		label := s.EventID
		if s.Timer {
			label = "timer"
		}
		trace = append(trace, label+"="+s.Phase)
		for _, x := range s.Transitions {
			trace = append(trace, x.Kind)
		}
	}
	want := "[event-1=pending event-2=pending timer=firing firing event-3=normal resolved]"
	if fmt.Sprint(trace) != want {
		t.Fatalf("trace %v\nwant  %v", trace, want)
	}
	if _, err := Simulate(context.Background(), SimRequest{Definition: json.RawMessage(`{"schema_version":1,"name":"x"}`)}, dsl.DefaultLimits(), false); err == nil {
		t.Fatal("invalid rule")
	}
}
