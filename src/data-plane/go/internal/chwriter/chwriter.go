// Package chwriter is the ClickHouse writer: a durable JetStream consumer that
// stores device events in ClickHouse in batches.
//
// Guarantees:
//   - A message is acknowledged only after the batch holding it was inserted.
//   - While ClickHouse is unavailable nothing is acknowledged and nothing is
//     dropped: the batch is retried in process (keeping the messages' ack timers
//     alive), then handed back to JetStream for later redelivery. The stream's
//     retention limits are the only bound on how long an outage can last.
//   - Inserts are idempotent on (organization, event id), so a redelivered
//     message is never stored twice.
package chwriter

import (
	"context"
	"log/slog"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
)

// Inserter stores events and reports, per event, whether it was new.
// storage.ClickHouse satisfies it.
type Inserter interface {
	InsertEvents(ctx context.Context, events []*envelope.Event) ([]bool, error)
}

// Config tunes batching and retries.
type Config struct {
	Name          string        // consumer name (metric label)
	BatchSize     int           // flush when this many messages are buffered
	FlushInterval time.Duration // flush at least this often when anything is buffered
	InsertTimeout time.Duration
	// RetryFor is how long one batch is retried in process before its messages are
	// handed back to JetStream (nak with delay). Keep it below the consumer's
	// AckWait, or rely on the in-progress signals sent while retrying.
	RetryFor   time.Duration
	RetryDelay time.Duration // nak delay once in-process retries are exhausted
	FetchMax   int           // messages per pull (<= BatchSize)
}

// Metrics of the writer.
type Metrics struct {
	InsertFailures prometheus.Counter
	InsertSeconds  prometheus.Histogram
	Rows           *prometheus.CounterVec // result: inserted|duplicate
	Buffered       prometheus.Gauge
}

// NewMetrics registers the writer metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	const ns = "meteorcloud_clickhouse_writer"
	m := &Metrics{
		InsertFailures: prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "insert_failures_total",
			Help: "Failed ClickHouse batch inserts (each is retried)."}),
		InsertSeconds: prometheus.NewHistogram(prometheus.HistogramOpts{Namespace: ns, Name: "insert_duration_seconds",
			Help: "Duration of successful batch inserts.", Buckets: prometheus.ExponentialBuckets(0.002, 2, 14)}),
		Rows: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "rows_total",
			Help: "Events written (inserted) or skipped because they were already stored (duplicate)."}, []string{"result"}),
		Buffered: prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Name: "buffered_messages",
			Help: "Messages waiting for the next flush."}),
	}
	reg.MustRegister(m.InsertFailures, m.InsertSeconds, m.Rows, m.Buffered)
	return m
}

// Writer consumes and writes.
type Writer struct {
	Cfg     Config
	Cons    jetstream.Consumer
	Store   Inserter
	NATS    *natsjs.Metrics
	Metrics *Metrics
	Log     *slog.Logger
	sleep   func(context.Context, time.Duration)
}

type item struct {
	msg jetstream.Msg
	ev  *envelope.Event
}

func (w *Writer) defaults() {
	if w.Cfg.BatchSize <= 0 {
		w.Cfg.BatchSize = 1000
	}
	if w.Cfg.FlushInterval <= 0 {
		w.Cfg.FlushInterval = time.Second
	}
	if w.Cfg.InsertTimeout <= 0 {
		w.Cfg.InsertTimeout = 15 * time.Second
	}
	if w.Cfg.RetryFor <= 0 {
		w.Cfg.RetryFor = 20 * time.Second
	}
	if w.Cfg.RetryDelay <= 0 {
		w.Cfg.RetryDelay = 10 * time.Second
	}
	if w.Cfg.FetchMax <= 0 || w.Cfg.FetchMax > w.Cfg.BatchSize {
		w.Cfg.FetchMax = w.Cfg.BatchSize
	}
	if w.sleep == nil {
		w.sleep = func(ctx context.Context, d time.Duration) {
			select {
			case <-ctx.Done():
			case <-time.After(d):
			}
		}
	}
}

// Run pulls, buffers and flushes until ctx ends, then flushes what it holds.
// Buffering never exceeds BatchSize, and nothing is pulled while a flush is
// running: a slow ClickHouse slows consumption instead of growing memory.
func (w *Writer) Run(ctx context.Context) {
	w.defaults()
	var buf []item
	deadline := time.Time{}
	for {
		if ctx.Err() != nil {
			if len(buf) > 0 {
				w.flush(context.Background(), buf)
			}
			return
		}
		wait := w.Cfg.FlushInterval
		if !deadline.IsZero() {
			wait = time.Until(deadline)
		}
		if wait > 0 {
			want := min(w.Cfg.FetchMax, w.Cfg.BatchSize-len(buf))
			res, err := w.Cons.Fetch(want, jetstream.FetchMaxWait(max(wait, 10*time.Millisecond)))
			if err != nil {
				w.Log.Warn("fetch failed", "consumer", w.Cfg.Name, "error", err.Error())
				w.sleep(ctx, time.Second)
			} else {
				var got []jetstream.Msg
				for msg := range res.Messages() {
					got = append(got, msg)
				}
				natsjs.Observe(got, w.Cfg.Name, w.NATS)
				for _, msg := range got {
					ev, derr := natsjs.DecodeEvent(msg)
					if derr != nil {
						w.NATS.Processed.WithLabelValues(w.Cfg.Name, "term").Inc()
						w.Log.Error("undecodable event terminated", "consumer", w.Cfg.Name, "subject", msg.Subject(), "error", derr.Error())
						_ = msg.TermWithReason("undecodable: " + derr.Error())
						continue
					}
					buf = append(buf, item{msg: msg, ev: ev})
				}
				if len(buf) > 0 && deadline.IsZero() {
					deadline = time.Now().Add(w.Cfg.FlushInterval)
				}
				w.Metrics.Buffered.Set(float64(len(buf)))
			}
		}
		if len(buf) > 0 && (len(buf) >= w.Cfg.BatchSize || !time.Now().Before(deadline)) {
			w.flush(ctx, buf)
			buf, deadline = nil, time.Time{}
			w.Metrics.Buffered.Set(0)
		}
	}
}

// flush inserts one batch, retrying while ClickHouse is unavailable, then acks
// (success) or naks (gave up for now) every message in it.
func (w *Writer) flush(ctx context.Context, batch []item) {
	start := time.Now()
	events := make([]*envelope.Event, len(batch))
	for i, it := range batch {
		events[i] = it.ev
	}
	w.NATS.BatchSize.WithLabelValues(w.Cfg.Name).Observe(float64(len(batch)))
	giveUp := time.Now().Add(w.Cfg.RetryFor)
	backoff := 200 * time.Millisecond
	for attempt := 1; ; attempt++ {
		ictx, cancel := context.WithTimeout(context.Background(), w.Cfg.InsertTimeout)
		t0 := time.Now()
		inserted, err := w.Store.InsertEvents(ictx, events)
		cancel()
		if err == nil {
			w.Metrics.InsertSeconds.Observe(time.Since(t0).Seconds())
			for i, it := range batch {
				if inserted[i] {
					w.Metrics.Rows.WithLabelValues("inserted").Inc()
				} else {
					w.Metrics.Rows.WithLabelValues("duplicate").Inc()
				}
				if aerr := it.msg.Ack(); aerr != nil {
					// Stored but not acknowledged: the redelivery is a harmless duplicate.
					w.Log.Warn("acknowledgement failed after insert", "consumer", w.Cfg.Name, "event_id", it.ev.ID, "error", aerr.Error())
					continue
				}
				natsjs.Acked(it.msg, w.Cfg.Name, w.NATS)
			}
			w.NATS.ProcessingTime.WithLabelValues(w.Cfg.Name).Observe(time.Since(start).Seconds())
			return
		}
		w.Metrics.InsertFailures.Inc()
		first := batch[0].ev
		w.Log.Warn("ClickHouse insert failed; batch kept unacknowledged", "consumer", w.Cfg.Name, "events", len(batch),
			"attempt", attempt, "first_organization_id", first.OrganizationID, "first_device_id", first.DeviceID,
			"first_event_id", first.ID, "error", err.Error())
		if ctx.Err() != nil || time.Now().Add(backoff).After(giveUp) {
			break
		}
		// Keep the ack timers alive while retrying so JetStream does not redeliver
		// these messages to another replica in the meantime.
		for _, it := range batch {
			_ = it.msg.InProgress()
		}
		w.sleep(ctx, backoff)
		backoff = min(backoff*2, 5*time.Second)
	}
	for _, it := range batch {
		w.NATS.Processed.WithLabelValues(w.Cfg.Name, "nak").Inc()
		_ = it.msg.NakWithDelay(w.Cfg.RetryDelay)
	}
	w.Log.Warn("ClickHouse unavailable; batch returned to JetStream for redelivery", "consumer", w.Cfg.Name,
		"events", len(batch), "retry_in", w.Cfg.RetryDelay.String())
}
