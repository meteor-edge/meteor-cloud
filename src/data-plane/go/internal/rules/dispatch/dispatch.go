// Package dispatch delivers the rule engine's outbox: alert records to the control
// plane, webhooks and notifications to integrations, device commands through the
// control plane's authorization, and platform events.
//
// Delivery never runs inside evaluation. Each action is claimed with a lease,
// delivered with a timeout, retried with backoff, and moved to the dead letter table
// after a permanent failure or too many attempts. An older pending action of the
// same rule, device, type and target is always delivered first, so a resolve never
// overtakes its firing. Delivery is at least once; receivers deduplicate with the
// idempotency key that every request carries.
package dispatch

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"slices"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

// Outcome of one successful delivery.
type Outcome string

const (
	Delivered Outcome = "delivered"
	// Skipped: nothing to send (integration disabled, alert silenced, target gone).
	Skipped Outcome = "skipped"
)

// Sender delivers one kind of action.
type Sender interface {
	Send(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (Outcome, error)
}

// GroupSender can deliver several actions of one organization at once.
type GroupSender interface {
	Sender
	// Groups says whether others may join this action, whether they may be of any rule
	// and transition, and how many may be sent together. Order inside a group is the
	// order they were queued in.
	Groups(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (ok, anyRule bool, limit int)
	// SendGroup answers one result per item.
	SendGroup(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) []Result
}

// Result of one action in a group.
type Result struct {
	Outcome Outcome
	Err     error
}

type permanent struct{ err error }

func (p permanent) Error() string { return p.err.Error() }
func (p permanent) Unwrap() error { return p.err }

// Permanent marks an error that retrying cannot fix; the action is dead-lettered at once.
func Permanent(err error) error { return permanent{err} }

// IsPermanent reports whether err was marked with Permanent.
func IsPermanent(err error) bool {
	var p permanent
	return errors.As(err, &p)
}

// Config tunes the dispatcher.
type Config struct {
	Workers     int             // concurrent deliveries
	Batch       int             // actions claimed per pass
	Lease       time.Duration   // how long a claimed action is hidden from other dispatchers
	Every       time.Duration   // pause between passes that found nothing
	GroupWait   time.Duration   // how far ahead of their time untried actions may join a group
	MaxAttempts int             // attempts before an action is dead-lettered
	Backoff     []time.Duration // wait before attempt n+1; the last entry repeats
}

func (c *Config) defaults() {
	if c.Workers <= 0 {
		c.Workers = 16
	}
	if c.Batch <= 0 || c.Batch > c.Workers {
		// Never claim more than can be delivered at once: an action waiting for a free
		// worker would use up its lease and be delivered twice.
		c.Batch = c.Workers
	}
	if c.Lease <= 0 {
		c.Lease = 2 * time.Minute
	}
	if c.Every <= 0 {
		c.Every = time.Second
	}
	if c.MaxAttempts <= 0 {
		c.MaxAttempts = 10
	}
	if len(c.Backoff) == 0 {
		c.Backoff = []time.Duration{5 * time.Second, 30 * time.Second, 2 * time.Minute, 10 * time.Minute, 30 * time.Minute}
	}
}

// Metrics of the dispatcher.
type Metrics struct {
	Results  *prometheus.CounterVec   // type, result: delivered|skipped|retry|dead
	Duration *prometheus.HistogramVec // type
}

// NewMetrics registers the dispatcher metrics (reg may be nil).
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Results: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: "meteorcloud_rules", Name: "actions_dispatched_total",
			Help: "Action delivery attempts, by action type and result."}, []string{"type", "result"}),
		Duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{Namespace: "meteorcloud_rules", Name: "action_delivery_seconds",
			Help: "Time to deliver one action.", Buckets: prometheus.DefBuckets}, []string{"type"}),
	}
	if reg != nil {
		reg.MustRegister(m.Results, m.Duration)
	}
	return m
}

// Dispatcher drains the outbox.
type Dispatcher struct {
	cfg     Config
	store   state.Outbox
	senders map[string]Sender
	clock   engine.Clock
	metrics *Metrics
	log     *slog.Logger
}

// New builds a dispatcher. Actions of a type without a sender are dead-lettered.
func New(cfg Config, store state.Outbox, senders map[string]Sender, clock engine.Clock, m *Metrics, log *slog.Logger) *Dispatcher {
	cfg.defaults()
	if clock == nil {
		clock = engine.SystemClock
	}
	if m == nil {
		m = NewMetrics(nil)
	}
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	return &Dispatcher{cfg: cfg, store: store, senders: senders, clock: clock, metrics: m, log: log}
}

// Run passes over the outbox until ctx ends.
func (d *Dispatcher) Run(ctx context.Context) {
	for {
		n, err := d.Pass(ctx)
		if err != nil && ctx.Err() == nil {
			d.log.Warn("outbox pass failed", "error", err.Error())
		}
		if n == d.cfg.Batch && err == nil {
			continue // more may be due right now
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(d.cfg.Every):
		}
	}
}

// Pass claims one batch of due actions and delivers it. It returns the number claimed.
func (d *Dispatcher) Pass(ctx context.Context) (int, error) {
	items, err := d.store.ClaimOutbox(ctx, d.clock.Now(), d.cfg.Lease, d.cfg.Batch)
	if err != nil {
		return 0, err
	}
	sem := make(chan struct{}, d.cfg.Workers)
	var wg sync.WaitGroup
	for _, group := range d.split(items) {
		sem <- struct{}{}
		wg.Add(1)
		go func() {
			defer func() { <-sem; wg.Done() }()
			d.deliver(ctx, group)
		}()
	}
	wg.Wait()
	return len(items), nil
}

// split puts claimed actions that may share a delivery together. Whether they really
// do is the sender's decision (see grow).
func (d *Dispatcher) split(items []state.OutboxItem) [][]state.OutboxItem {
	var out [][]state.OutboxItem
	index := map[[5]string]int{}
	for _, it := range items {
		if _, ok := d.senders[it.ActionType].(GroupSender); !ok {
			out = append(out, []state.OutboxItem{it})
			continue
		}
		k := [5]string{it.OrgID, it.ActionType, it.Target, it.RuleID, it.Transition}
		if it.ActionType == dsl.ActionAlert {
			k[3], k[4] = "", ""
		}
		if i, ok := index[k]; ok {
			out[i] = append(out[i], it)
			continue
		}
		index[k] = len(out)
		out = append(out, []state.OutboxItem{it})
	}
	return out
}

// deliver sends a group (often of one) and records each result.
func (d *Dispatcher) deliver(ctx context.Context, group []state.OutboxItem) {
	start := time.Now()
	group, results := d.sendGroup(ctx, group)
	d.metrics.Duration.WithLabelValues(group[0].ActionType).Observe(time.Since(start).Seconds())
	now := d.clock.Now()
	jitter := rand.Float64()
	var done []int64
	var serr error
	for i, it := range group {
		outcome, err := results[i].Outcome, results[i].Err
		log := d.log.With("idempotency_key", it.Key, "action_type", it.ActionType, "organization_id", it.OrgID,
			"rule_id", it.RuleID, "device_id", it.DeviceID, "attempt", it.Attempts)
		switch {
		case err == nil:
			d.metrics.Results.WithLabelValues(it.ActionType, string(outcome)).Inc()
			done = append(done, it.ID)
			if outcome == Skipped {
				log.Info("action skipped")
			}
		case IsPermanent(err) || it.Attempts >= d.cfg.MaxAttempts:
			d.metrics.Results.WithLabelValues(it.ActionType, "dead").Inc()
			log.Warn("action dead-lettered", "error", err.Error())
			serr = errors.Join(serr, d.store.MarkDead(ctx, it, err.Error(), now))
		default:
			d.metrics.Results.WithLabelValues(it.ActionType, "retry").Inc()
			// One jitter for the group, so it is retried as one message.
			next := now.Add(d.backoff(it.Attempts, jitter))
			log.Info("action delivery failed; will retry", "error", err.Error(), "next_attempt_at", next)
			serr = errors.Join(serr, d.store.Reschedule(ctx, it.ID, next, err.Error()))
		}
	}
	if len(done) > 0 {
		serr = errors.Join(serr, d.store.MarkDelivered(ctx, now, done...))
	}
	if serr != nil {
		// The lease runs out and the action is claimed again: at least once.
		d.log.Warn("recording delivery result failed", "error", serr.Error())
	}
}

// sendGroup adds what else can go along, sends, and answers the final group with one
// result per action.
func (d *Dispatcher) sendGroup(ctx context.Context, group []state.OutboxItem) (out []state.OutboxItem, results []Result) {
	defer func() {
		if r := recover(); r != nil {
			out, results = group, make([]Result, len(group))
			for i := range results {
				results[i] = Result{Err: Permanent(fmt.Errorf("sender panic: %v", r))}
			}
		}
	}()
	one := func(it state.OutboxItem) Result {
		out, err := d.send(ctx, it)
		return Result{out, err}
	}
	each := func() []Result {
		// At once, like separate groups: one after another could outlast the lease.
		res := make([]Result, len(group))
		var wg sync.WaitGroup
		for i, it := range group {
			wg.Add(1)
			go func() {
				defer wg.Done()
				res[i] = one(it)
			}()
		}
		wg.Wait()
		return res
	}
	gs, ok := d.senders[group[0].ActionType].(GroupSender)
	if !ok {
		return group, each()
	}
	first, err := payloadOf(group[0])
	if err != nil {
		return group, each()
	}
	ok, anyRule, limit := gs.Groups(ctx, group[0], first)
	if !ok {
		return group, each()
	}
	now := d.clock.Now()
	if room := limit - len(group); room > 0 {
		more, err := d.store.ClaimMore(ctx, group[0], anyRule, now, now.Add(d.cfg.GroupWait), d.cfg.Lease, room)
		if err != nil {
			d.log.Warn("claiming more actions failed", "error", err.Error())
		}
		group = append(group, more...)
	}
	slices.SortFunc(group, func(a, b state.OutboxItem) int { return cmp.Compare(a.ID, b.ID) })
	results = make([]Result, len(group))
	var items []state.OutboxItem
	var ps []*engine.ActionPayload
	var at []int
	for i, it := range group {
		p, err := payloadOf(it)
		if err != nil {
			results[i] = Result{Err: err}
			continue
		}
		items, ps, at = append(items, it), append(ps, p), append(at, i)
	}
	if len(items) == 1 {
		results[at[0]] = one(items[0])
		return group, results
	}
	for j, r := range gs.SendGroup(ctx, items, ps) {
		results[at[j]] = r
	}
	return group, results
}

func payloadOf(it state.OutboxItem) (*engine.ActionPayload, error) {
	var p engine.ActionPayload
	if err := json.Unmarshal(it.Payload, &p); err != nil {
		return nil, Permanent(fmt.Errorf("decode payload: %w", err))
	}
	return &p, nil
}

func (d *Dispatcher) send(ctx context.Context, it state.OutboxItem) (out Outcome, err error) {
	defer func() {
		if r := recover(); r != nil {
			out, err = "", Permanent(fmt.Errorf("sender panic: %v", r))
		}
	}()
	p, err := payloadOf(it)
	if err != nil {
		return "", err
	}
	s, ok := d.senders[it.ActionType]
	if !ok {
		return "", Permanent(fmt.Errorf("no sender for action type %q", it.ActionType))
	}
	return s.Send(ctx, it, p)
}

// backoff before the attempt after `attempts`, with ±10% jitter so retries spread out.
func (d *Dispatcher) backoff(attempts int, jitter float64) time.Duration {
	i := attempts - 1
	if i < 0 {
		i = 0
	}
	if i >= len(d.cfg.Backoff) {
		i = len(d.cfg.Backoff) - 1
	}
	b := d.cfg.Backoff[i]
	return b - b/10 + time.Duration(jitter*float64(b/5))
}
