package engine

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
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
		_ = inParallel(e.cfg.TimerWorkers, due, func(t state.Timer) error {
			if err := e.Tick(ctx, t); err != nil {
				// The lease expires and another pass retries it.
				e.log.Error("timer evaluation failed", "organization_id", logSafe(t.OrgID), "device_id", logSafe(t.DeviceID),
					"rule_id", logSafe(t.RuleID), "error", logSafe(err.Error()))
			}
			return nil
		})
		fired += len(due)
		if len(due) < e.cfg.TimerBatch {
			if timers, outbox, err := e.store.Counts(ctx); err == nil {
				e.metrics.PendingTimers.Set(float64(timers))
				e.metrics.OutboxBacklog.Set(float64(outbox))
			}
			return fired, nil
		}
	}
}

// inParallel runs fn over items with n workers and returns the first error, after which
// no further item is started. Evaluations of one device still run one at a time (device
// locks), and running many together lets them share group commits.
func inParallel[T any](n int, items []T, fn func(T) error) error {
	var (
		wg     sync.WaitGroup
		once   sync.Once
		failed atomic.Bool
		first  error
	)
	next := make(chan T)
	for range min(n, len(items)) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for it := range next {
				if failed.Load() {
					continue
				}
				if err := fn(it); err != nil {
					once.Do(func() { first = err })
					failed.Store(true)
				}
			}
		}()
	}
	for _, it := range items {
		next <- it
	}
	close(next)
	wg.Wait()
	return first
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
	cur, err := e.store.LoadCursor(ctx, t.DeviceID)
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	wall := e.clock.Now()
	now := wall
	if cur != nil && cur.Watermark.After(now) {
		// The device clock runs ahead: time must not move back behind its newest event.
		now = cur.Watermark
	}
	isNew := false
	if st == nil {
		st, isNew = e.newState(r, dev, wall), true
	}
	if st.RuleVersion > r.Version {
		return ErrStale // the lease expires and a later pass has the newer snapshot
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
		st.LastEvaluated = wall
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
	var ps []pending
	if st.Phase == state.PhaseFiring {
		t := Transition{Kind: TransitionResolved, Reason: reason, Episode: st.Episode, At: e.clock.Now()}
		if r == nil {
			// The rule is gone from the snapshot: only the alert action can still run,
			// so the open alert is closed in the control plane.
			r = &Rule{ID: st.RuleID, Version: st.RuleVersion, Actions: defaultAlertOnly}
			r.Def = retiredDef
		}
		ps = append(ps, pending{r, st, t})
		e.enqueue(b, ps)
	}
	if !st.Retired() {
		st.Retire(e.clock.Now())
		b.States = []*state.RuleState{st}
	}
	if err := e.commits.commit(ctx, b); err != nil {
		return err
	}
	e.afterCommit(ps)
	return nil
}

// Reconcile compares the stored evaluation state with the current rules, so open
// alerts close without waiting for an event when a rule was removed, disabled or
// changed, or when a device left its scope. Working from the stored state (not from
// the previous snapshot) also covers what changed while no worker was running. It
// seeds absence timers for devices without state, so "device never sent anything"
// fires too, including for devices added after the rule. Call it again after an error.
func (e *Engine) Reconcile(ctx context.Context, cur *RuleSet) error {
	ids, err := e.store.RuleIDs(ctx)
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	for _, id := range ids {
		r, exists := cur.Rule(id)
		if err := e.eachState(ctx, id, e.cfg.TimerWorkers, func(st *state.RuleState) error {
			if st.Retired() {
				return nil
			}
			dev, okDev := cur.Device(st.DeviceID)
			switch {
			case !exists:
				return e.retireLocked(ctx, st, nil, ReasonRemoved)
			case !okDev || !r.Matches(dev):
				return e.retireLocked(ctx, st, r, ReasonNoDevice)
			case st.RuleVersion > r.Version:
				return nil // another worker has a newer snapshot
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

// retireLocked retires under the device lock, from a fresh copy whenever another worker
// wrote the state first.
func (e *Engine) retireLocked(ctx context.Context, st *state.RuleState, r *Rule, reason string) error {
	unlock := e.locks.lock(st.DeviceID)
	defer unlock()
	for attempt := 0; ; attempt++ {
		err := e.retire(ctx, st, r, reason)
		if !errors.Is(err, state.ErrConflict) || attempt >= e.cfg.Retries {
			return err
		}
		e.metrics.Conflicts.Inc()
		states, err := e.store.LoadStates(ctx, st.DeviceID, []string{st.RuleID})
		if err != nil {
			e.metrics.StateErrors.WithLabelValues("load").Inc()
			return err
		}
		fresh := states[st.RuleID]
		if fresh == nil || fresh.Retired() {
			return nil // another worker retired it
		}
		st = fresh
	}
}

func (e *Engine) eachState(ctx context.Context, ruleID string, workers int, fn func(*state.RuleState) error) error {
	after := ""
	for {
		page, err := e.store.StatesForRule(ctx, ruleID, after, 500)
		if err != nil {
			return err
		}
		if err := inParallel(workers, page, fn); err != nil {
			return err
		}
		if len(page) > 0 {
			after = page[len(page)-1].DeviceID
		}
		if len(page) < 500 {
			return nil
		}
	}
}

func (e *Engine) seedAbsence(ctx context.Context, rs *RuleSet, r *Rule) error {
	known := map[string]bool{}
	if err := e.eachState(ctx, r.ID, 1, func(st *state.RuleState) error {
		known[st.DeviceID] = st.ConditionHash == r.Hash
		return nil
	}); err != nil {
		return err
	}
	var missing []*Device
	for _, d := range rs.MatchingDevices(r) {
		if !known[d.ID] {
			missing = append(missing, d)
		}
	}
	return inParallel(e.cfg.TimerWorkers, missing, func(d *Device) error {
		return e.Tick(ctx, state.Timer{RuleID: r.ID, DeviceID: d.ID, OrgID: d.OrganizationID, Due: e.clock.Now()})
	})
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
