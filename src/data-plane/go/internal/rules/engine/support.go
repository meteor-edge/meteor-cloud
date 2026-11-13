package engine

import (
	"context"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/rules/state"
)

// Clock is injectable so time-dependent behaviour is tested without waiting.
type Clock interface{ Now() time.Time }

type systemClock struct{}

func (systemClock) Now() time.Time { return time.Now().UTC() }

// SystemClock is the wall clock.
var SystemClock Clock = systemClock{}

// FakeClock is a manually advanced clock for tests and rule simulation.
type FakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func NewFakeClock(t time.Time) *FakeClock { return &FakeClock{t: t.UTC()} }

func (c *FakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *FakeClock) Set(t time.Time) {
	c.mu.Lock()
	c.t = t.UTC()
	c.mu.Unlock()
}

func (c *FakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// Metrics are the engine's Prometheus metrics. Labels are bounded: never an
// organization, device or rule id.
type Metrics struct {
	EventsReceived  prometheus.Counter
	EventsEvaluated prometheus.Counter
	EventsDropped   *prometheus.CounterVec // reason
	Evaluations     *prometheus.CounterVec // result: fire|pending|clear
	EvalLatency     prometheus.Histogram
	Transitions     *prometheus.CounterVec // transition
	ActionsQueued   *prometheus.CounterVec // type
	StateErrors     *prometheus.CounterVec // op
	Conflicts       prometheus.Counter
	CommitLatency   prometheus.Histogram
	CommitBatch     prometheus.Histogram
	TimersFired     prometheus.Counter
	ActiveRules     prometheus.Gauge
	RuleLoadErrors  prometheus.Gauge
	ActiveAlerts    prometheus.Gauge
	PendingTimers   prometheus.Gauge
	OutboxBacklog   prometheus.Gauge
	SnapshotAge     prometheus.Gauge
}

// NewMetrics registers the engine metrics on reg (nil: an unregistered set, for tests).
func NewMetrics(reg prometheus.Registerer) *Metrics {
	const ns, sub = "meteorcloud", "rules"
	reg2 := func(c prometheus.Collector) {
		if reg != nil {
			reg.MustRegister(c)
		}
	}
	counter := func(name, help string) prometheus.Counter {
		c := prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Subsystem: sub, Name: name, Help: help})
		reg2(c)
		return c
	}
	vec := func(name, help string, labels ...string) *prometheus.CounterVec {
		c := prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Subsystem: sub, Name: name, Help: help}, labels)
		reg2(c)
		return c
	}
	gauge := func(name, help string) prometheus.Gauge {
		g := prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Subsystem: sub, Name: name, Help: help})
		reg2(g)
		return g
	}
	hist := func(name, help string, b []float64) prometheus.Histogram {
		h := prometheus.NewHistogram(prometheus.HistogramOpts{Namespace: ns, Subsystem: sub, Name: name, Help: help, Buckets: b})
		reg2(h)
		return h
	}
	lat := []float64{.0005, .001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5}
	return &Metrics{
		EventsReceived:  counter("events_received_total", "Events handed to the rule engine."),
		EventsEvaluated: counter("events_evaluated_total", "Events evaluated against at least one rule."),
		EventsDropped:   vec("events_dropped_total", "Events not evaluated, by reason.", "reason"),
		Evaluations:     vec("evaluations_total", "Rule evaluations, by outcome.", "result"),
		EvalLatency:     hist("evaluation_seconds", "Time to evaluate and commit one event or timer.", lat),
		Transitions:     vec("transitions_total", "Alert transitions decided by the engine.", "transition"),
		ActionsQueued:   vec("actions_queued_total", "Actions written to the outbox, by type.", "type"),
		StateErrors:     vec("state_errors_total", "State persistence errors, by operation.", "op"),
		Conflicts:       counter("state_conflicts_total", "Optimistic-lock conflicts (another worker wrote first)."),
		CommitLatency:   hist("commit_seconds", "Duration of one group commit.", lat),
		CommitBatch:     hist("commit_batch_size", "Evaluations per group commit.", []float64{1, 2, 5, 10, 25, 50, 100, 250, 500}),
		TimersFired:     counter("timers_fired_total", "Timer re-evaluations run."),
		ActiveRules:     gauge("active", "Enabled rules loaded from the snapshot."),
		RuleLoadErrors:  gauge("load_errors", "Snapshot rules refused by validation."),
		ActiveAlerts:    gauge("alerts_firing", "Rule and device pairs firing, as seen by this worker since start."),
		PendingTimers:   gauge("timers_pending", "Stored timers (pending scheduled evaluations)."),
		OutboxBacklog:   gauge("outbox_backlog", "Actions waiting in the outbox."),
		SnapshotAge:     gauge("snapshot_loaded_timestamp_seconds", "When the current rule snapshot was loaded."),
	}
}

// committer batches concurrent commits into one store call (group commit), so each
// event is acknowledged only after its state is durable without paying one
// transaction per event.
type committer struct {
	store    state.Store
	metrics  *Metrics
	maxBatch int
	linger   time.Duration
	reqs     chan *commitReq
	stop     chan struct{}
	done     chan struct{}
}

type commitReq struct {
	batch *state.Batch
	err   chan error
}

func newCommitter(s state.Store, m *Metrics, maxBatch int, linger time.Duration) *committer {
	c := &committer{store: s, metrics: m, maxBatch: maxBatch, linger: linger,
		reqs: make(chan *commitReq, maxBatch*4), stop: make(chan struct{}), done: make(chan struct{})}
	go c.run()
	return c
}

func (c *committer) commit(ctx context.Context, b *state.Batch) error {
	if b.Empty() {
		return nil
	}
	r := &commitReq{batch: b, err: make(chan error, 1)}
	select {
	case c.reqs <- r:
	case <-ctx.Done():
		return ctx.Err()
	case <-c.stop:
		return context.Canceled
	}
	select {
	case err := <-r.err:
		return err
	case <-ctx.Done():
		// The commit may still land; the event is redelivered and deduplicated.
		return ctx.Err()
	}
}

func (c *committer) run() {
	defer close(c.done)
	for {
		var first *commitReq
		select {
		case first = <-c.reqs:
		case <-c.stop:
			return
		}
		group := []*commitReq{first}
		timer := time.NewTimer(c.linger)
	collect:
		for len(group) < c.maxBatch {
			select {
			case r := <-c.reqs:
				group = append(group, r)
			case <-timer.C:
				break collect
			}
		}
		timer.Stop()
		batches := make([]*state.Batch, len(group))
		for i, r := range group {
			batches[i] = r.batch
		}
		start := time.Now()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		errs := c.store.CommitMany(ctx, batches)
		cancel()
		c.metrics.CommitLatency.Observe(time.Since(start).Seconds())
		c.metrics.CommitBatch.Observe(float64(len(group)))
		for i, r := range group {
			r.err <- errs[i]
		}
	}
}

func (c *committer) close() {
	close(c.stop)
	<-c.done
}

// stripes serialize work on one device without a global lock.
type stripes struct{ mu []sync.Mutex }

func newStripes(n int) *stripes { return &stripes{mu: make([]sync.Mutex, n)} }

func (s *stripes) lock(key string) func() {
	var h uint32 = 2166136261
	for i := 0; i < len(key); i++ {
		h ^= uint32(key[i])
		h *= 16777619
	}
	m := &s.mu[h%uint32(len(s.mu))]
	m.Lock()
	return m.Unlock
}
