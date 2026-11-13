package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

// SimEvent is one sample event for a rule test.
type SimEvent struct {
	ID     string          `json:"id,omitempty"`
	Time   time.Time       `json:"time"`
	Stream string          `json:"stream,omitempty"`
	Data   json.RawMessage `json:"data"`
}

// SimRequest asks to run a rule definition over sample events with a virtual clock.
// Until, when set, keeps the clock running after the last event so timers (absence,
// duration, cooldown) can fire.
type SimRequest struct {
	Definition json.RawMessage `json:"definition"`
	Events     []SimEvent      `json:"events"`
	Until      *time.Time      `json:"until,omitempty"`
}

// SimStep is the rule's state after one event or timer.
type SimStep struct {
	At          time.Time          `json:"at"`
	EventID     string             `json:"event_id,omitempty"`
	Timer       bool               `json:"timer,omitempty"`
	Phase       string             `json:"phase"`
	Transitions []Transition       `json:"transitions,omitempty"`
	Values      map[string]float64 `json:"values,omitempty"`
	Skipped     string             `json:"skipped,omitempty"`
}

// SimResult is the full trace.
type SimResult struct {
	Steps []SimStep `json:"steps"`
}

// MaxSimEvents bounds one rule test.
const MaxSimEvents = 1000

// Simulate evaluates a rule over sample events in an isolated engine (in-memory
// state, fake clock). It never touches real state and never runs actions.
func Simulate(ctx context.Context, req SimRequest, lim dsl.Limits, allowPlatform bool) (*SimResult, error) {
	if len(req.Events) > MaxSimEvents {
		return nil, fmt.Errorf("at most %d events per test", MaxSimEvents)
	}
	def, err := dsl.ParseAndValidate(req.Definition, lim, allowPlatform)
	if err != nil {
		return nil, err
	}
	const org = "simulation"
	orgID := org
	if def.Scope.Type == dsl.ScopePlatform {
		orgID = ""
	}
	dev := Device{ID: "simulated-device", OrganizationID: org, GroupID: def.Scope.GroupID,
		DeviceTypeID: def.Scope.DeviceTypeID, Labels: def.Scope.Labels}
	if len(def.Scope.DeviceIDs) > 0 {
		dev.ID = def.Scope.DeviceIDs[0]
	}
	snap := &Snapshot{Version: "simulation", Devices: []Device{dev},
		Rules: []RuleRecord{{ID: "rule", OrganizationID: orgID, Version: 1, Definition: req.Definition}}}
	rs := CompileSnapshot(snap, lim)
	if len(rs.Errors) > 0 {
		return nil, fmt.Errorf("%s", rs.Errors[0].Error)
	}
	if rs.Len() == 0 {
		return &SimResult{}, nil // a disabled rule never evaluates
	}
	events := append([]SimEvent(nil), req.Events...)
	start := time.Now().UTC()
	if len(events) > 0 {
		start = events[0].Time
		for _, ev := range events {
			if ev.Time.Before(start) {
				start = ev.Time
			}
		}
	}
	clock := NewFakeClock(start)
	store := state.NewMemory()
	e := New(Config{CommitLinger: time.Microsecond, CommitBatch: 1, TimerLease: 24 * time.Hour}, store, clock, nil, nil)
	defer e.Close()
	e.Apply(rs)
	if err := e.Reconcile(ctx, nil, rs); err != nil {
		return nil, err
	}
	res := &SimResult{}
	seen := 0
	record := func(at time.Time, id string, timer bool, skipped string) error {
		states, err := store.LoadStates(ctx, dev.ID, []string{"rule"})
		if err != nil {
			return err
		}
		step := SimStep{At: at, EventID: id, Timer: timer, Phase: state.PhaseNormal, Skipped: skipped}
		if st := states["rule"]; st != nil {
			step.Phase = st.Phase
			step.Values = st.LastValues
		}
		for _, o := range store.Outbox()[seen:] {
			if o.ActionType != dsl.ActionAlert {
				continue
			}
			var p ActionPayload
			_ = json.Unmarshal(o.Payload, &p)
			step.Transitions = append(step.Transitions, p.Transition)
		}
		seen = len(store.Outbox())
		res.Steps = append(res.Steps, step)
		return nil
	}
	runTimersUntil := func(limit time.Time) error {
		for {
			ts := store.Timers()
			sort.Slice(ts, func(i, j int) bool { return ts[i].Due.Before(ts[j].Due) })
			if len(ts) == 0 || ts[0].Due.After(limit) {
				return nil
			}
			if ts[0].Due.After(clock.Now()) {
				clock.Set(ts[0].Due)
			}
			if _, err := e.FireDue(ctx); err != nil {
				return err
			}
			if err := record(clock.Now(), "", true, ""); err != nil {
				return err
			}
			if len(res.Steps) > 4*MaxSimEvents {
				return fmt.Errorf("the test produced too many timer steps")
			}
		}
	}
	for i, se := range events {
		if err := runTimersUntil(se.Time); err != nil {
			return nil, err
		}
		if se.Time.After(clock.Now()) {
			clock.Set(se.Time)
		}
		id := se.ID
		if id == "" {
			id = fmt.Sprintf("event-%d", i+1)
		}
		stream := se.Stream
		if stream == "" {
			stream = def.Streams()[0]
		}
		fields, err := eval.FlattenJSON(se.Data)
		if err != nil {
			return nil, fmt.Errorf("events[%d]: %v", i, err)
		}
		ev := &eval.Event{ID: id, OrgID: org, DeviceID: dev.ID, Kind: "telemetry", Stream: stream, Time: se.Time.UTC(), Fields: fields}
		skipped := ""
		if r, _ := rs.Rule("rule"); !r.Prog.Reads(stream) {
			skipped = "the rule does not read this stream"
		}
		if err := e.Handle(ctx, ev); err != nil {
			return nil, fmt.Errorf("events[%d]: %v", i, err)
		}
		if err := record(se.Time, id, false, skipped); err != nil {
			return nil, err
		}
	}
	if req.Until != nil {
		if err := runTimersUntil(*req.Until); err != nil {
			return nil, err
		}
	}
	return res, nil
}
