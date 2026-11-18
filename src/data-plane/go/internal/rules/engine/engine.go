// Package engine runs compiled rules over normalized events: candidate selection,
// deduplication, ordering, evaluation, the alert state machine, timers and the
// action outbox. It knows nothing about brokers, NATS or HTTP; an adapter turns
// whatever transport delivers into eval.Event and calls Handle.
package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

// ErrPermanent marks an event that can never be processed (malformed). The
// transport should terminate it instead of redelivering.
var ErrPermanent = errors.New("event cannot be processed")

// ErrNotReady means no rule snapshot has been loaded yet.
var ErrNotReady = errors.New("rule engine has no snapshot yet")

// Transition kinds.
const (
	TransitionFiring   = "firing"
	TransitionResolved = "resolved"
)

// Resolve reasons.
const (
	ReasonCleared  = "condition_cleared"
	ReasonUpdated  = "rule_updated"
	ReasonRemoved  = "rule_removed"
	ReasonNoDevice = "device_out_of_scope"
)

// Transition is one alert lifecycle change decided by the engine.
type Transition struct {
	Kind        string             `json:"transition"`
	Reason      string             `json:"reason,omitempty"`
	Episode     int64              `json:"episode"`
	Fingerprint string             `json:"fingerprint,omitempty"`
	At          time.Time          `json:"at"`
	EventID     string             `json:"event_id,omitempty"`
	Values      map[string]float64 `json:"values,omitempty"`
}

// ActionPayload is what the outbox carries to the dispatcher for one action.
type ActionPayload struct {
	RuleID         string     `json:"rule_id"`
	RuleVersion    int        `json:"rule_version"`
	RuleName       string     `json:"rule_name"`
	OrganizationID string     `json:"organization_id"`
	DeviceID       string     `json:"device_id"`
	Severity       string     `json:"severity"`
	Transition     Transition `json:"event"`
	Action         dsl.Action `json:"action"`
}

// Config tunes the engine.
type Config struct {
	Stripes      int           // device lock stripes
	CommitBatch  int           // evaluations per group commit
	CommitLinger time.Duration // how long a group commit waits to fill
	Retries      int           // re-evaluations after an optimistic-lock conflict
	TimerLease   time.Duration // how long a claimed timer is hidden from other workers
	TimerBatch   int           // timers claimed per scheduler pass
	TimerEvery   time.Duration // scheduler pass interval
	Limits       dsl.Limits
}

func (c *Config) defaults() {
	if c.Stripes <= 0 {
		c.Stripes = 1024
	}
	if c.CommitBatch <= 0 {
		c.CommitBatch = 500
	}
	if c.CommitLinger <= 0 {
		c.CommitLinger = 20 * time.Millisecond
	}
	if c.Retries <= 0 {
		c.Retries = 3
	}
	if c.TimerLease <= 0 {
		c.TimerLease = 30 * time.Second
	}
	if c.TimerBatch <= 0 {
		c.TimerBatch = 500
	}
	if c.TimerEvery <= 0 {
		c.TimerEvery = time.Second
	}
	if c.Limits.MaxNodes == 0 {
		c.Limits = dsl.DefaultLimits()
	}
}

// Engine evaluates rules. It is safe for concurrent use.
type Engine struct {
	cfg     Config
	store   state.Store
	clock   Clock
	metrics *Metrics
	log     *slog.Logger
	rules   atomic.Pointer[RuleSet]
	locks   *stripes
	commits *committer
	firing  atomic.Int64
}

// New creates an engine. Call Close to stop its committer.
func New(cfg Config, store state.Store, clock Clock, metrics *Metrics, log *slog.Logger) *Engine {
	cfg.defaults()
	if clock == nil {
		clock = SystemClock
	}
	if metrics == nil {
		metrics = NewMetrics(nil)
	}
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	return &Engine{cfg: cfg, store: store, clock: clock, metrics: metrics, log: log,
		locks: newStripes(cfg.Stripes), commits: newCommitter(store, metrics, cfg.CommitBatch, cfg.CommitLinger)}
}

// Close stops the committer. In-flight Handle calls fail.
func (e *Engine) Close() { e.commits.close() }

// Rules returns the current rule set (nil before the first snapshot).
func (e *Engine) Rules() *RuleSet { return e.rules.Load() }

// Ready reports whether a snapshot is loaded.
func (e *Engine) Ready() bool { return e.rules.Load() != nil }

// Apply installs a compiled snapshot and returns the previous one.
func (e *Engine) Apply(rs *RuleSet) *RuleSet {
	old := e.rules.Swap(rs)
	e.metrics.ActiveRules.Set(float64(rs.Len()))
	e.metrics.RuleLoadErrors.Set(float64(len(rs.Errors)))
	e.metrics.SnapshotAge.Set(float64(e.clock.Now().Unix()))
	for _, re := range rs.Errors {
		e.log.Warn("rule refused", "rule_id", re.RuleID, "error", re.Error)
	}
	return old
}

// Handle evaluates one event. nil means the event is fully processed (or was a
// duplicate, or is not relevant) and may be acknowledged. An error wrapping
// ErrPermanent must not be retried; any other error should be retried later.
func (e *Engine) Handle(ctx context.Context, ev *eval.Event) error {
	start := time.Now()
	e.metrics.EventsReceived.Inc()
	rs := e.rules.Load()
	if rs == nil {
		return ErrNotReady
	}
	if ev.ID == "" || ev.OrgID == "" || ev.DeviceID == "" || ev.Time.IsZero() {
		e.metrics.EventsDropped.WithLabelValues("invalid").Inc()
		return fmt.Errorf("%w: event id, organization, device and time are required", ErrPermanent)
	}
	dev, ok := rs.Device(ev.DeviceID)
	if !ok {
		e.metrics.EventsDropped.WithLabelValues("unknown_device").Inc()
		return nil
	}
	if dev.OrganizationID != ev.OrgID {
		// Never evaluate an event against another tenant's rules.
		e.metrics.EventsDropped.WithLabelValues("organization_mismatch").Inc()
		e.log.Warn("event organization does not own the device", "organization_id", ev.OrgID, "device_id", ev.DeviceID, "event_id", ev.ID)
		return nil
	}
	cands := rs.Candidates(dev, ev.Stream)
	if len(cands) == 0 {
		e.metrics.EventsDropped.WithLabelValues("no_rules").Inc()
		return nil
	}
	unlock := e.locks.lock(ev.DeviceID)
	defer unlock()
	var err error
	for attempt := 0; attempt <= e.cfg.Retries; attempt++ {
		if err = e.process(ctx, ev, dev, cands); !errors.Is(err, state.ErrConflict) {
			break
		}
		e.metrics.Conflicts.Inc()
	}
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("commit").Inc()
		e.log.Error("rule evaluation not committed", "organization_id", ev.OrgID, "device_id", ev.DeviceID, "event_id", ev.ID, "error", err)
		return err
	}
	e.metrics.EventsEvaluated.Inc()
	e.metrics.EvalLatency.Observe(time.Since(start).Seconds())
	return nil
}

func (e *Engine) process(ctx context.Context, ev *eval.Event, dev *Device, cands []*Rule) error {
	cur, err := e.store.LoadCursor(ctx, ev.DeviceID)
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	if cur == nil {
		cur = &state.Cursor{DeviceID: ev.DeviceID, OrgID: ev.OrgID}
	}
	if cur.Seen(ev.ID) {
		e.metrics.EventsDropped.WithLabelValues("duplicate").Inc()
		return nil
	}
	ids := make([]string, len(cands))
	for i, r := range cands {
		ids[i] = r.ID
	}
	states, err := e.store.LoadStates(ctx, ev.DeviceID, ids)
	if err != nil {
		e.metrics.StateErrors.WithLabelValues("load").Inc()
		return err
	}
	ooo := ev.Time.Before(cur.Watermark)
	frontier := ev.Time
	if ooo {
		frontier = cur.Watermark
	}
	wall := e.clock.Now()
	batch := &state.Batch{Cursor: cur}
	var trans []pending
	for _, r := range cands {
		if ev.Time.Before(cur.Watermark.Add(-r.Def.LateBy())) {
			e.metrics.EventsDropped.WithLabelValues("late").Inc()
			continue
		}
		st, isNew := states[r.ID], false
		if st == nil {
			st, isNew = e.newState(r, dev, wall), true
		}
		before := snapshotOf(st)
		ts := e.reconcileVersion(st, r, frontier)
		out := r.Prog.Evaluate(eval.Input{Event: ev, Now: frontier, OutOfOrder: ooo,
			LateTolerance: r.Def.LateBy(), ActivatedAt: st.ActivatedAt}, st.Nodes)
		ts = append(ts, e.advance(st, r, out, frontier, ev, ooo)...)
		e.countOutcome(out)
		e.planTimer(st, out, false, batch)
		if len(ts) > 0 || isNew || snapshotOf(st) != before {
			st.LastEvaluated = wall
			st.LastEventID = ev.ID
			batch.States = append(batch.States, st)
		}
		for _, t := range ts {
			trans = append(trans, pending{r, st, t})
		}
	}
	cur.Remember(ev.ID)
	if ev.Time.After(cur.Watermark) {
		cur.Watermark = ev.Time
	}
	cur.LastSeen = wall
	e.enqueue(batch, trans)
	if err := e.commits.commit(ctx, batch); err != nil {
		return err
	}
	e.afterCommit(trans)
	return nil
}

type pending struct {
	rule *Rule
	st   *state.RuleState
	t    Transition
}

func (e *Engine) newState(r *Rule, dev *Device, now time.Time) *state.RuleState {
	return &state.RuleState{RuleID: r.ID, DeviceID: dev.ID, OrgID: dev.OrganizationID, RuleVersion: r.Version,
		ConditionHash: r.Hash, Phase: state.PhaseNormal, PhaseSince: now, ActivatedAt: now, Nodes: eval.State{}}
}

// snapshotOf serializes a state for change detection, ignoring bookkeeping fields,
// so an evaluation that changes nothing writes nothing.
func snapshotOf(st *state.RuleState) string {
	c := *st
	c.LastEvaluated, c.LastEventID, c.Version = time.Time{}, "", 0
	b, _ := json.Marshal(&c)
	return string(b)
}

// reconcileVersion handles a rule that changed since the state was written. A
// presentation-only change keeps the state; a changed condition, scope or source
// discards it deliberately and resolves an open alert with reason rule_updated.
func (e *Engine) reconcileVersion(st *state.RuleState, r *Rule, now time.Time) []Transition {
	st.RuleVersion = r.Version
	if st.ConditionHash == r.Hash {
		return nil
	}
	var out []Transition
	if st.Phase == state.PhaseFiring {
		out = append(out, Transition{Kind: TransitionResolved, Reason: ReasonUpdated, Episode: st.Episode, At: now})
		e.firing.Add(-1)
	}
	st.ConditionHash = r.Hash
	st.Nodes = eval.State{}
	st.Phase, st.PhaseSince, st.ActiveAlerts, st.CooldownUntil = state.PhaseNormal, now, 0, time.Time{}
	st.ActivatedAt = now
	return out
}

// advance runs the alert state machine for one evaluation.
func (e *Engine) advance(st *state.RuleState, r *Rule, out eval.Output, now time.Time, ev *eval.Event, ooo bool) []Transition {
	var ts []Transition
	eventID := ""
	if ev != nil {
		eventID = ev.ID
	}
	fire := func() {
		st.Episode++
		st.Phase, st.PhaseSince, st.ActiveAlerts = state.PhaseFiring, now, 1
		st.LastValues = out.Values
		ts = append(ts, Transition{Kind: TransitionFiring, Episode: st.Episode, At: now, EventID: eventID, Values: out.Values})
		e.firing.Add(1)
	}
	if st.Phase == state.PhaseCooldown && !now.Before(st.CooldownUntil) {
		st.Phase, st.PhaseSince, st.CooldownUntil = state.PhaseNormal, now, time.Time{}
	}
	switch st.Phase {
	case state.PhaseNormal:
		if out.Fire {
			fire()
		} else if out.Pending {
			st.Phase, st.PhaseSince = state.PhasePending, now
		}
	case state.PhasePending:
		if out.Fire {
			fire()
		} else if !out.Pending {
			st.Phase, st.PhaseSince = state.PhaseNormal, now
		}
	case state.PhaseFiring:
		resolve := !out.Fire
		if out.Resolve != nil { // hysteresis: only the resolve condition closes the alert
			resolve = *out.Resolve && !out.Fire
		}
		if resolve {
			ts = append(ts, Transition{Kind: TransitionResolved, Reason: ReasonCleared, Episode: st.Episode, At: now, EventID: eventID, Values: out.Values})
			e.firing.Add(-1)
			st.ActiveAlerts = 0
			if cd := r.Def.Cooldown.D(); cd > 0 {
				st.Phase, st.PhaseSince, st.CooldownUntil = state.PhaseCooldown, now, now.Add(cd)
			} else {
				st.Phase, st.PhaseSince = state.PhaseNormal, now
			}
		} else if mode, max := r.Def.Mode(); mode == dsl.ModePerEvent && out.Fire && ev != nil && !ooo && st.ActiveAlerts < max {
			st.ActiveAlerts++
			ts = append(ts, Transition{Kind: TransitionFiring, Episode: st.Episode, Fingerprint: ev.ID, At: now, EventID: ev.ID, Values: out.Values})
		}
	}
	for _, t := range ts {
		e.metrics.Transitions.WithLabelValues(t.Kind).Inc()
	}
	e.metrics.ActiveAlerts.Set(float64(e.firing.Load()))
	return ts
}

func (e *Engine) countOutcome(out eval.Output) {
	switch {
	case out.Fire:
		e.metrics.Evaluations.WithLabelValues("fire").Inc()
	case out.Pending:
		e.metrics.Evaluations.WithLabelValues("pending").Inc()
	default:
		e.metrics.Evaluations.WithLabelValues("clear").Inc()
	}
}

// planTimer keeps at most one stored timer per rule and device. A later deadline
// does not rewrite an earlier stored timer: that timer fires early, re-evaluates
// and reschedules. So an absence rule writes its timer once per silence, not on
// every event.
func (e *Engine) planTimer(st *state.RuleState, out eval.Output, isTick bool, b *state.Batch) {
	want := out.Deadline
	if st.Phase == state.PhaseCooldown {
		want = minTime(want, st.CooldownUntil)
	}
	key := st.Key()
	switch {
	case want.IsZero():
		if isTick && !st.TimerDue.IsZero() {
			b.TimersClear = append(b.TimersClear, key)
			st.TimerDue = time.Time{}
		}
	case isTick || st.TimerDue.IsZero() || want.Before(st.TimerDue):
		b.TimersSet = append(b.TimersSet, state.Timer{RuleID: st.RuleID, DeviceID: st.DeviceID, OrgID: st.OrgID, Due: want})
		st.TimerDue = want
	}
}

func minTime(a, b time.Time) time.Time {
	if a.IsZero() {
		return b
	}
	if b.IsZero() || a.Before(b) {
		return a
	}
	return b
}

// IdempotencyKey identifies one action for one transition. Replays of the same
// transition produce the same key, which the outbox stores uniquely.
func IdempotencyKey(ruleID, deviceID string, t Transition, actionIndex int) string {
	k := fmt.Sprintf("%s:%s:%d:%s", ruleID, deviceID, t.Episode, t.Kind)
	if t.Fingerprint != "" {
		k += ":" + t.Fingerprint
	}
	if t.Reason == ReasonUpdated || t.Reason == ReasonRemoved || t.Reason == ReasonNoDevice {
		k += ":" + t.Reason
	}
	return fmt.Sprintf("%s:a%d", k, actionIndex)
}

func (e *Engine) enqueue(b *state.Batch, ts []pending) {
	for _, p := range ts {
		for i, a := range p.rule.Actions {
			if !contains(a.On, p.t.Kind) {
				continue
			}
			sev := a.Severity
			if sev == "" {
				sev = p.rule.Def.EffectiveSeverity()
			}
			payload, _ := json.Marshal(ActionPayload{RuleID: p.rule.ID, RuleVersion: p.rule.Version, RuleName: p.rule.Def.Name,
				OrganizationID: p.st.OrgID, DeviceID: p.st.DeviceID, Severity: sev, Transition: p.t, Action: a})
			b.Outbox = append(b.Outbox, state.OutboxItem{Key: IdempotencyKey(p.rule.ID, p.st.DeviceID, p.t, i),
				OrgID: p.st.OrgID, RuleID: p.rule.ID, RuleVersion: p.rule.Version, DeviceID: p.st.DeviceID,
				Transition: p.t.Kind, ActionType: a.Type, Payload: payload, Status: state.OutboxPending,
				NextAttempt: p.t.At, CreatedAt: e.clock.Now()})
		}
	}
}

func (e *Engine) afterCommit(ts []pending) {
	for _, p := range ts {
		for _, a := range p.rule.Actions {
			if contains(a.On, p.t.Kind) {
				e.metrics.ActionsQueued.WithLabelValues(a.Type).Inc()
			}
		}
		e.log.Info("alert transition", "organization_id", p.st.OrgID, "device_id", p.st.DeviceID, "rule_id", p.rule.ID,
			"rule_version", p.rule.Version, "transition", p.t.Kind, "reason", p.t.Reason, "episode", p.t.Episode, "event_id", p.t.EventID)
	}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
