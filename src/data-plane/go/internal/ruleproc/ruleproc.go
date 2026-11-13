// Package ruleproc is the rule processor's JetStream consumer: it pulls device
// events from its own durable consumer and hands each one to a Handler.
//
// The rule logic itself lives behind the Handler interface (the rule engine in
// internal/rules plugs in there). This package owns delivery: per-device
// ordering inside a replica, explicit acknowledgement, retries with backoff,
// bounded batches and metrics.
package ruleproc

import (
	"context"
	"errors"
	"hash/fnv"
	"log/slog"
	"sync"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
)

// Handler processes one event. A nil error acknowledges the message. Any other
// error redelivers it later with backoff; wrap an error with Permanent when
// retrying can never help, and the message is terminated instead.
//
// Delivery is at least once: a handler sees the same event id again after a
// crash, a timeout or a redelivery, and must be idempotent on ev.ID.
type Handler interface {
	Handle(ctx context.Context, ev *envelope.Event) error
}

// HandlerFunc adapts a function to Handler.
type HandlerFunc func(ctx context.Context, ev *envelope.Event) error

func (f HandlerFunc) Handle(ctx context.Context, ev *envelope.Event) error { return f(ctx, ev) }

type permanent struct{ err error }

func (p permanent) Error() string { return p.err.Error() }
func (p permanent) Unwrap() error { return p.err }

// Permanent marks an error that retrying cannot fix.
func Permanent(err error) error { return permanent{err} }

// IsPermanent reports whether err was marked with Permanent.
func IsPermanent(err error) bool {
	var p permanent
	return errors.As(err, &p)
}

// Config tunes the processor.
type Config struct {
	Name           string          // consumer name, used as a metric label
	Workers        int             // parallel workers; one device always maps to the same worker
	HandlerTimeout time.Duration   // per event
	RetryBackoff   []time.Duration // nak delay by delivery attempt
	FetchBatch     int
	FetchMaxWait   time.Duration
}

// Processor consumes events and calls the handler.
type Processor struct {
	Cfg     Config
	Cons    jetstream.Consumer
	Handler Handler
	Metrics *natsjs.Metrics
	Log     *slog.Logger
}

// worker index for a message: same organization and device, same worker, so one
// device's events are handled in stream order within this replica.
func (p *Processor) slot(subject string, n int) int {
	h := fnv.New32a()
	h.Write([]byte(subject))
	return int(h.Sum32() % uint32(n))
}

// Run processes until ctx ends; the batch in progress is finished first.
func (p *Processor) Run(ctx context.Context) {
	workers := max(p.Cfg.Workers, 1)
	natsjs.Batches(ctx, p.Cons, p.Cfg.Name, p.Cfg.FetchBatch, p.Cfg.FetchMaxWait, p.Metrics, p.Log,
		func(msgs []jetstream.Msg) {
			start := time.Now()
			p.Metrics.BatchSize.WithLabelValues(p.Cfg.Name).Observe(float64(len(msgs)))
			lanes := make([][]jetstream.Msg, workers)
			for _, m := range msgs {
				i := p.slot(m.Subject(), workers)
				lanes[i] = append(lanes[i], m)
			}
			var wg sync.WaitGroup
			for _, lane := range lanes {
				if len(lane) == 0 {
					continue
				}
				wg.Add(1)
				go func(lane []jetstream.Msg) {
					defer wg.Done()
					for _, m := range lane {
						p.one(m)
					}
				}(lane)
			}
			wg.Wait()
			p.Metrics.ProcessingTime.WithLabelValues(p.Cfg.Name).Observe(time.Since(start).Seconds())
		})
}

func (p *Processor) one(msg jetstream.Msg) {
	name := p.Cfg.Name
	ev, err := natsjs.DecodeEvent(msg)
	if err != nil {
		// Never decodable, never retried: terminate and keep the reason visible.
		p.Metrics.Processed.WithLabelValues(name, "term").Inc()
		p.Log.Error("undecodable event terminated", "consumer", name, "subject", msg.Subject(), "error", err.Error())
		_ = msg.TermWithReason("undecodable: " + err.Error())
		return
	}
	timeout := p.Cfg.HandlerTimeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	err = p.Handler.Handle(ctx, ev)
	cancel()
	switch {
	case err == nil:
		if aerr := msg.Ack(); aerr != nil {
			p.Log.Warn("acknowledgement failed; the event will be redelivered", "consumer", name, "event_id", ev.ID, "error", aerr.Error())
			return
		}
		natsjs.Acked(msg, name, p.Metrics)
	case IsPermanent(err):
		p.Metrics.Processed.WithLabelValues(name, "term").Inc()
		p.Log.Error("event failed permanently; terminated", "consumer", name, "organization_id", ev.OrganizationID,
			"device_id", ev.DeviceID, "event_id", ev.ID, "error", err.Error())
		_ = msg.TermWithReason(err.Error())
	default:
		var attempt uint64 = 1
		if meta, merr := msg.Metadata(); merr == nil {
			attempt = meta.NumDelivered
		}
		delay := natsjs.RetryDelay(attempt, p.Cfg.RetryBackoff)
		p.Metrics.Processed.WithLabelValues(name, "nak").Inc()
		p.Log.Warn("event processing failed; retrying", "consumer", name, "organization_id", ev.OrganizationID,
			"device_id", ev.DeviceID, "event_id", ev.ID, "attempt", attempt, "retry_in", delay.String(), "error", err.Error())
		_ = msg.NakWithDelay(delay)
	}
}

// Dedupe skips events whose id was handled successfully recently. It protects a
// non-idempotent handler from JetStream redeliveries in one process; durable,
// cross-replica de-duplication belongs in the handler's own state.
func Dedupe(next Handler, size int) Handler {
	if size <= 0 {
		size = 100000
	}
	d := &dedupe{next: next, size: size, seen: make(map[string]struct{}, size)}
	return d
}

type dedupe struct {
	next  Handler
	size  int
	mu    sync.Mutex
	seen  map[string]struct{}
	order []string
}

func (d *dedupe) Handle(ctx context.Context, ev *envelope.Event) error {
	key := ev.OrganizationID + "/" + ev.ID
	d.mu.Lock()
	_, dup := d.seen[key]
	d.mu.Unlock()
	if dup {
		return nil
	}
	if err := d.next.Handle(ctx, ev); err != nil {
		return err
	}
	d.mu.Lock()
	if _, ok := d.seen[key]; !ok {
		d.seen[key] = struct{}{}
		d.order = append(d.order, key)
		if len(d.order) > d.size {
			delete(d.seen, d.order[0])
			d.order = d.order[1:]
		}
	}
	d.mu.Unlock()
	return nil
}

// LogHandler is the default handler until a rule engine is plugged in: it counts
// events by kind and logs a sample, showing where real-time rules run.
type LogHandler struct {
	Log    *slog.Logger
	Events *prometheus.CounterVec // kind
}

func (h *LogHandler) Handle(_ context.Context, ev *envelope.Event) error {
	if h.Events != nil {
		h.Events.WithLabelValues(ev.Kind).Inc()
	}
	h.Log.Debug("event evaluated", "organization_id", ev.OrganizationID, "device_id", ev.DeviceID,
		"event_id", ev.ID, "kind", ev.Kind, "type", ev.Type)
	return nil
}
