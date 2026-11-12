// Package ingest is the write path: a bounded queue in front of batched, retried,
// idempotent storage writes, followed by live distribution.
//
// Delivery semantics (contracts/data-plane-v1.md):
//   - Submit returns only after the events are durably stored (or were already
//     stored: duplicates are reported, not written twice).
//   - When the queue is full Submit fails immediately with ErrOverloaded; nothing is
//     buffered beyond the configured bound and nothing is dropped silently.
//   - Live distribution happens after storage and is best effort (at most once).
package ingest

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

var (
	// ErrOverloaded means the bounded queue is full: retry later.
	ErrOverloaded = errors.New("ingestion queue is full")
	// ErrStopped means the pipeline is shutting down.
	ErrStopped = errors.New("ingestion pipeline is stopped")
)

// Result statuses.
const (
	StatusStored    = "stored"
	StatusDuplicate = "duplicate"
)

// Config tunes the pipeline.
type Config struct {
	QueueSize    int           // maximum events waiting for storage
	Workers      int           // concurrent storage writers
	MaxBatch     int           // events per storage write
	Linger       time.Duration // how long a writer waits to fill a batch
	WriteRetries int           // extra attempts after a failed write
	RetryBackoff time.Duration // first backoff, doubled per attempt
	WriteTimeout time.Duration // per storage write
}

func (c *Config) defaults() {
	if c.QueueSize <= 0 {
		c.QueueSize = 10000
	}
	if c.Workers <= 0 {
		c.Workers = 4
	}
	if c.MaxBatch <= 0 {
		c.MaxBatch = 500
	}
	if c.Linger <= 0 {
		c.Linger = 5 * time.Millisecond
	}
	if c.WriteRetries < 0 {
		c.WriteRetries = 0
	}
	if c.RetryBackoff <= 0 {
		c.RetryBackoff = 100 * time.Millisecond
	}
	if c.WriteTimeout <= 0 {
		c.WriteTimeout = 10 * time.Second
	}
}

type request struct {
	events   []*envelope.Event
	received time.Time
	statuses []string
	err      error
	done     chan struct{}
}

// Pipeline accepts normalized events.
type Pipeline struct {
	cfg     Config
	store   storage.Store
	bus     bus.Bus
	metrics *observability.Metrics
	log     *slog.Logger

	queue chan *request
	mu    sync.Mutex
	depth int // events queued or being written
	stop  chan struct{}
	wg    sync.WaitGroup
	once  sync.Once
}

// New creates a pipeline; call Start before Submit.
func New(cfg Config, store storage.Store, b bus.Bus, metrics *observability.Metrics, log *slog.Logger) *Pipeline {
	cfg.defaults()
	p := &Pipeline{cfg: cfg, store: store, bus: b, metrics: metrics, log: log,
		queue: make(chan *request, cfg.QueueSize), stop: make(chan struct{})}
	metrics.QueueCapacity.Set(float64(cfg.QueueSize))
	return p
}

// Start launches the storage writers.
func (p *Pipeline) Start() {
	for i := 0; i < p.cfg.Workers; i++ {
		p.wg.Add(1)
		go p.worker()
	}
}

// Stop drains queued requests, then stops the writers. Submit fails afterwards.
func (p *Pipeline) Stop(ctx context.Context) {
	p.once.Do(func() { close(p.stop) })
	done := make(chan struct{})
	go func() { p.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
	}
}

// Depth is the number of events queued or being written.
func (p *Pipeline) Depth() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.depth
}

func (p *Pipeline) reserve(n int) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.depth+n > p.cfg.QueueSize {
		return false
	}
	p.depth += n
	p.metrics.QueueDepth.Set(float64(p.depth))
	return true
}

func (p *Pipeline) release(n int) {
	p.mu.Lock()
	p.depth -= n
	p.metrics.QueueDepth.Set(float64(p.depth))
	p.mu.Unlock()
}

// Submit stores the events and returns one status per event, in order.
func (p *Pipeline) Submit(ctx context.Context, events []*envelope.Event) ([]string, error) {
	if len(events) == 0 {
		return nil, nil
	}
	select {
	case <-p.stop:
		return nil, ErrStopped
	default:
	}
	if !p.reserve(len(events)) {
		return nil, ErrOverloaded
	}
	req := &request{events: events, received: time.Now(), done: make(chan struct{})}
	select {
	case p.queue <- req:
	default:
		// Cannot happen while depth accounting holds (the channel is as large as the
		// event bound), but never block a caller on it.
		p.release(len(events))
		return nil, ErrOverloaded
	}
	select {
	case <-req.done:
		return req.statuses, req.err
	case <-ctx.Done():
		// The write may still complete; the caller retries with the same ids, which
		// is safe because writes are idempotent.
		return nil, ctx.Err()
	}
}

func (p *Pipeline) worker() {
	defer p.wg.Done()
	for {
		var first *request
		select {
		case first = <-p.queue:
		case <-p.stop:
			// Drain what was accepted before the stop.
			select {
			case first = <-p.queue:
			default:
				return
			}
		}
		batch := []*request{first}
		count := len(first.events)
		timer := time.NewTimer(p.cfg.Linger)
	fill:
		for count < p.cfg.MaxBatch {
			select {
			case next := <-p.queue:
				batch = append(batch, next)
				count += len(next.events)
			case <-timer.C:
				break fill
			}
		}
		timer.Stop()
		p.write(batch, count)
	}
}

func (p *Pipeline) write(batch []*request, count int) {
	events := make([]*envelope.Event, 0, count)
	for _, r := range batch {
		events = append(events, r.events...)
	}
	var inserted []bool
	var err error
	backoff := p.cfg.RetryBackoff
	for attempt := 0; ; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), p.cfg.WriteTimeout)
		start := time.Now()
		inserted, err = p.store.InsertEvents(ctx, events)
		cancel()
		p.metrics.StorageWrite.Observe(time.Since(start).Seconds())
		if err == nil {
			break
		}
		p.metrics.StorageErrors.WithLabelValues("insert_events").Inc()
		if attempt >= p.cfg.WriteRetries {
			break
		}
		time.Sleep(backoff)
		backoff *= 2
	}
	if err != nil {
		p.log.Error("storage write failed; callers are told to retry", "events", count, "error", err.Error())
		p.metrics.MessagesProcessed.WithLabelValues("failed").Add(float64(count))
	}
	offset := 0
	now := time.Now()
	for _, r := range batch {
		n := len(r.events)
		if err != nil {
			r.err = err
		} else {
			r.statuses = make([]string, n)
			for i := 0; i < n; i++ {
				if inserted[offset+i] {
					r.statuses[i] = StatusStored
					p.metrics.MessagesProcessed.WithLabelValues("stored").Inc()
				} else {
					r.statuses[i] = StatusDuplicate
					p.metrics.MessagesProcessed.WithLabelValues("duplicate").Inc()
				}
			}
			p.metrics.IngestLatency.Observe(now.Sub(r.received).Seconds())
		}
		offset += n
		p.release(n)
		close(r.done)
	}
	if err != nil {
		return
	}
	// Distribute only what was newly stored: a redelivered duplicate is not re-sent.
	for i, ev := range events {
		if !inserted[i] {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		if perr := p.bus.PublishLive(ctx, ev); perr != nil {
			p.metrics.FailedDeliveries.WithLabelValues("live").Inc()
			p.log.Warn("live distribution failed; the event is stored and can be queried",
				"event_id", ev.ID, "error", perr.Error())
		}
		cancel()
	}
}
