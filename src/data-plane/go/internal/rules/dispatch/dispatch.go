// Package dispatch delivers the rule engine's outbox: alert records to the control
// plane, webhooks and notifications to integrations, device commands through the
// control plane's authorization, and platform events.
//
// Delivery never runs inside evaluation. Each action is claimed with a lease,
// delivered with a timeout, retried with backoff, and moved to the dead letter table
// after a permanent failure or too many attempts. An older pending action of the
// same rule, device and type is always delivered first, so a resolve never
// overtakes its firing. Delivery is at least once; receivers deduplicate with the
// idempotency key that every request carries.
package dispatch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"

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
	MaxAttempts int             // attempts before an action is dead-lettered
	Backoff     []time.Duration // wait before attempt n+1; the last entry repeats
}

func (c *Config) defaults() {
	if c.Workers <= 0 {
		c.Workers = 8
	}
	if c.Batch <= 0 {
		c.Batch = 100
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
	for _, it := range items {
		sem <- struct{}{}
		wg.Add(1)
		go func(it state.OutboxItem) {
			defer func() { <-sem; wg.Done() }()
			d.deliver(ctx, it)
		}(it)
	}
	wg.Wait()
	return len(items), nil
}

func (d *Dispatcher) deliver(ctx context.Context, it state.OutboxItem) {
	start := time.Now()
	outcome, err := d.send(ctx, it)
	d.metrics.Duration.WithLabelValues(it.ActionType).Observe(time.Since(start).Seconds())
	now := d.clock.Now()
	log := d.log.With("idempotency_key", it.Key, "action_type", it.ActionType, "organization_id", it.OrgID,
		"rule_id", it.RuleID, "device_id", it.DeviceID, "attempt", it.Attempts)
	var serr error
	switch {
	case err == nil:
		d.metrics.Results.WithLabelValues(it.ActionType, string(outcome)).Inc()
		serr = d.store.MarkDelivered(ctx, it.ID, now)
		if outcome == Skipped {
			log.Info("action skipped")
		}
	case IsPermanent(err) || it.Attempts >= d.cfg.MaxAttempts:
		d.metrics.Results.WithLabelValues(it.ActionType, "dead").Inc()
		log.Warn("action dead-lettered", "error", err.Error())
		serr = d.store.MarkDead(ctx, it, err.Error(), now)
	default:
		d.metrics.Results.WithLabelValues(it.ActionType, "retry").Inc()
		next := now.Add(d.backoff(it.Attempts))
		log.Info("action delivery failed; will retry", "error", err.Error(), "next_attempt_at", next)
		serr = d.store.Reschedule(ctx, it.ID, next, err.Error())
	}
	if serr != nil {
		// The lease runs out and the action is claimed again: at least once.
		log.Warn("recording delivery result failed", "error", serr.Error())
	}
}

func (d *Dispatcher) send(ctx context.Context, it state.OutboxItem) (out Outcome, err error) {
	defer func() {
		if r := recover(); r != nil {
			out, err = "", Permanent(fmt.Errorf("sender panic: %v", r))
		}
	}()
	var p engine.ActionPayload
	if err := json.Unmarshal(it.Payload, &p); err != nil {
		return "", Permanent(fmt.Errorf("decode payload: %w", err))
	}
	s, ok := d.senders[it.ActionType]
	if !ok {
		return "", Permanent(fmt.Errorf("no sender for action type %q", it.ActionType))
	}
	return s.Send(ctx, it, &p)
}

// backoff before the attempt after `attempts`, with ±10% jitter so retries spread out.
func (d *Dispatcher) backoff(attempts int) time.Duration {
	i := attempts - 1
	if i < 0 {
		i = 0
	}
	if i >= len(d.cfg.Backoff) {
		i = len(d.cfg.Backoff) - 1
	}
	b := d.cfg.Backoff[i]
	return b - b/10 + time.Duration(rand.Int64N(int64(b/5)+1))
}
