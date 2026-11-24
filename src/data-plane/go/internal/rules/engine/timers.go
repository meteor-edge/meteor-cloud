package engine

import (
	"context"
	"errors"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

// RunTimers claims due timers and re-evaluates them until ctx ends. One loop per
// worker serves every device: there is no per-device polling.
func (e *Engine) RunTimers(ctx context.Context) {
	t := time.NewTicker(e.cfg.TimerEvery)
	defer t.Stop()
	for {
		if _, err := e.FireDue(ctx); err != nil && ctx.Err() == nil {
			e.log.Error("timer pass failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// FireDue runs one scheduler pass and returns how many timers fired.
func (e *Engine) FireDue(ctx context.Context) (int, error) {
	if e.rules.Load() == nil {
		return 0, nil
	}
	fired := 0
	for {
		now := e.clock.Now()
		due, err := e.store.ClaimTimers(ctx, now, e.cfg.TimerLease, e.cfg.TimerBatch)
		if err != nil {
			e.metrics.StateErrors.WithLabelValues("timers").Inc()
			return fired, err
		}
		for _, t := range due {
			if err := e.Tick(ctx, t); err != nil {
				// The lease expires and another pass retries it.
				e.log.Error("timer evaluation failed", "organization_id", t.OrgID, "device_id", t.DeviceID, "rule_id", t.RuleID, "error", err)
			}
			fired++
		}
		if len(due) < e.cfg.TimerBatch {
			if timers, outbox, err := e.store.Counts(ctx); err == nil {
				e.metrics.PendingTimers.Set(float64(timers))
				e.metrics.OutboxBacklog.Set(float64(outbox))
			}
			return fired, nil
		}
	}
}

// Tick re-evaluates one rule on one device at the current time, without an event.
func (e *Engine) Tick(ctx context.Context, t state.Timer) error {
	e.metrics.TimersFired.Inc()
	start := time.Now()
	unlock := e.locks.lock(t.DeviceID)
	defer unlock()
	var err error
	for attempt := 0; attempt <= e.cfg.Retries; attempt++ {
		if err = e.tick(ctx, t); !errors.Is(err, state.ErrConflict) {
			break
		}
		e.metrics.Conflicts.Inc()
	}
	if err == nil {
		e.metrics.EvalLatency.Observe(time.Since(start).Seconds())
	}
	return err
}

func (e *Engine) tick(ctx context.Context, t state.Timer) error {
	rs := e.rules.Load()
	key := state.Key{RuleID: t.RuleID, DeviceID: t.DeviceID}
	r, okRule := rs.Rule(t.RuleID)
	dev, okDev := rs.Device(t.DeviceID)
	states, err := e.store.LoadStates(ctx, t.DeviceID, []string{t.RuleID})
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	st := states[t.RuleID]
	if !okRule || !okDev || !r.Matches(dev) {
		// Rule gone or device out of scope: reconciliation resolves and removes state.
		b := &state.Batch{TimersClear: []state.Key{key}}
		if st != nil {
			if !okRule {
				return e.retire(ctx, st, nil, ReasonRemoved)
			}
			return e.retire(ctx, st, r, ReasonNoDevice)
		}
		return e.commits.commit(ctx, b)
	}
	now := e.clock.Now()
	isNew := false
	if st == nil {
		st, isNew = e.newState(r, dev, now), true
	}
	before := snapshotOf(st)
	ts := e.reconcileVersion(st, r, now)
	out := r.Prog.Evaluate(eval.Input{Now: now, LateTolerance: r.Def.LateBy(), ActivatedAt: st.ActivatedAt}, st.Nodes)
	ts = append(ts, e.advance(st, r, out, now, nil, false)...)
	e.countOutcome(out)
	b := &state.Batch{}
	st.TimerDue = t.Due // the claimed timer exists until this commit clears or moves it
	e.planTimer(st, out, true, b)
	if len(ts) > 0 || isNew || snapshotOf(st) != before {
		st.LastEvaluated = now
		b.States = append(b.States, st)
	} else if len(b.TimersSet)+len(b.TimersClear) > 0 {
		b.States = append(b.States, st) // keep TimerDue in step with the timer row
	}
	var ps []pending
	for _, tr := range ts {
		ps = append(ps, pending{r, st, tr})
	}
	e.enqueue(b, ps)
	if err := e.commits.commit(ctx, b); err != nil {
		return err
	}
	e.afterCommit(ps)
	return nil
}

// retire resolves an open alert of a removed or disabled rule (or of a device that
// left the rule's scope) and drops its evaluation state and timer. The record itself
// stays as a tombstone, because it carries the episode counter: episodes are part of
// the idempotency keys, so a rule that is enabled again must continue counting or its
// next alert would look like a replay of an old one and never be sent.
func (e *Engine) retire(ctx context.Context, st *state.RuleState, r *Rule, reason string) error {
	b := &state.Batch{TimersClear: []state.Key{st.Key()}}
	if st.Phase == state.PhaseFiring {
		t := Transition{Kind: TransitionResolved, Reason: reason, Episode: st.Episode, At: e.clock.Now()}
		e.firing.Add(-1)
		e.metrics.Transitions.WithLabelValues(t.Kind).Inc()
		if r == nil {
			// The rule is gone from the snapshot: only the alert action can still run,
			// so the open alert is closed in the control plane.
			r = &Rule{ID: st.RuleID, Version: st.RuleVersion, Actions: defaultAlertOnly}
			r.Def = retiredDef
		}
		e.enqueue(b, []pending{{r, st, t}})
	}
	if !st.Retired() {
		st.Retire(e.clock.Now())
		b.States = []*state.RuleState{st}
	}
	return e.commits.commit(ctx, b)
}

// Reconcile compares the stored evaluation state with the current rules, so open
// alerts close without waiting for an event when a rule was removed, disabled or
// changed, or when a device left its scope. Working from the stored state (not from
// the previous snapshot) also covers what changed while no worker was running. It
// seeds absence timers for devices without state, so "device never sent anything"
// fires too, including for devices added after the rule.
func (e *Engine) Reconcile(ctx context.Context, cur *RuleSet) error {
	ids, err := e.store.RuleIDs(ctx)
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	for _, id := range ids {
		r, exists := cur.Rule(id)
		if err := e.eachState(ctx, id, func(st *state.RuleState) error {
			if st.Retired() {
				return nil
			}
			dev, okDev := cur.Device(st.DeviceID)
			switch {
			case !exists:
				return e.retireLocked(ctx, st, nil, ReasonRemoved)
			case !okDev || !r.Matches(dev):
				return e.retireLocked(ctx, st, r, ReasonNoDevice)
			case st.ConditionHash != r.Hash:
				return e.Tick(ctx, state.Timer{RuleID: st.RuleID, DeviceID: st.DeviceID, OrgID: st.OrgID, Due: e.clock.Now()})
			}
			return nil
		}); err != nil {
			return err
		}
	}
	for _, r := range cur.Rules() {
		if r.Prog.HasAbsence() {
			if err := e.seedAbsence(ctx, cur, r); err != nil {
				return err
			}
		}
	}
	return nil
}

func (e *Engine) retireLocked(ctx context.Context, st *state.RuleState, r *Rule, reason string) error {
	unlock := e.locks.lock(st.DeviceID)
	defer unlock()
	return e.retire(ctx, st, r, reason)
}

func (e *Engine) eachState(ctx context.Context, ruleID string, fn func(*state.RuleState) error) error {
	after := ""
	for {
		page, err := e.store.StatesForRule(ctx, ruleID, after, 500)
		if err != nil {
			return err
		}
		for _, st := range page {
			err := fn(st)
			if err != nil && !errors.Is(err, state.ErrConflict) {
				return err
			}
			after = st.DeviceID
		}
		if len(page) < 500 {
			return nil
		}
	}
}

func (e *Engine) seedAbsence(ctx context.Context, rs *RuleSet, r *Rule) error {
	known := map[string]bool{}
	if err := e.eachState(ctx, r.ID, func(st *state.RuleState) error {
		known[st.DeviceID] = st.ConditionHash == r.Hash
		return nil
	}); err != nil {
		return err
	}
	for _, d := range rs.MatchingDevices(r) {
		if known[d.ID] {
			continue
		}
		if err := e.Tick(ctx, state.Timer{RuleID: r.ID, DeviceID: d.ID, OrgID: d.OrganizationID, Due: e.clock.Now()}); err != nil {
			return err
		}
	}
	return nil
}

// Status pages through the evaluation state of one rule.
func (e *Engine) Status(ctx context.Context, ruleID, after string, limit int) ([]*state.RuleState, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	return e.store.StatesForRule(ctx, ruleID, after, limit)
}

// Used to close the alert of a rule that is no longer in the snapshot.
var (
	defaultAlertOnly = []dsl.Action{{Type: dsl.ActionAlert, On: []string{dsl.OnFiring, dsl.OnResolved}}}
	retiredDef       = &dsl.Rule{SchemaVersion: dsl.SchemaVersion, Name: "removed rule"}
)
