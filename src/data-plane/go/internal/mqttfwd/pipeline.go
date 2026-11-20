package mqttfwd

import (
	"context"
	"hash/fnv"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Message is one device message waiting to be forwarded.
type Message struct {
	Topic    string
	Payload  []byte
	Received time.Time
	// Ack tells the broker the message is done. Nil for QoS 0. It is called
	// once the control plane accepted the message or it was given up on, never
	// for a message that is still owed (those are sent again by the broker).
	Ack func()
}

// Forwarder delivers one message to the control plane (IngestClient).
type Forwarder interface {
	Forward(ctx context.Context, topic string, payload []byte) error
}

// Pipeline is a bounded queue split into shards. A device's messages always
// land on the same shard and one worker drains each shard in order, so status
// changes of one device (online, offline) are applied in the order they were
// published while different devices are forwarded in parallel.
type Pipeline struct {
	shards      []chan Message
	fwd         Forwarder
	batch       int
	maxAttempts int
	metrics     *Metrics
	log         *slog.Logger

	busy    atomic.Int64
	wg      sync.WaitGroup
	stopped chan struct{} // closed when the workers stop, so Offer never blocks forever

	// backoff returns the wait before attempt n+1; tests shorten it.
	backoff func(attempt int) time.Duration
}

// NewPipeline prepares workers shards holding queueSize messages in total.
func NewPipeline(workers, queueSize, batch, maxAttempts int, fwd Forwarder, m *Metrics, log *slog.Logger) *Pipeline {
	per := (queueSize + workers - 1) / workers
	p := &Pipeline{fwd: fwd, batch: batch, maxAttempts: maxAttempts, metrics: m, log: log,
		backoff: defaultBackoff, stopped: make(chan struct{})}
	for range workers {
		p.shards = append(p.shards, make(chan Message, per))
	}
	m.QueueCapacity.Set(float64(per * workers))
	return p
}

func defaultBackoff(attempt int) time.Duration {
	return min(100*time.Millisecond<<min(attempt-1, 6), 5*time.Second)
}

// Start runs one worker per shard until ctx ends.
func (p *Pipeline) Start(ctx context.Context) {
	for _, ch := range p.shards {
		p.wg.Add(1)
		go func() {
			defer p.wg.Done()
			p.work(ctx, ch)
		}()
	}
	go func() {
		<-ctx.Done()
		close(p.stopped)
	}()
}

// Wait returns once every worker has stopped.
func (p *Pipeline) Wait() { p.wg.Wait() }

// Idle reports whether nothing is queued or being forwarded.
func (p *Pipeline) Idle() bool { return p.metrics.queued.Load() == 0 && p.busy.Load() == 0 }

// Offer queues m on its device's shard. With wait it blocks until there is
// room (QoS 1/2: the broker's Receive Maximum keeps that short) or the
// workers stopped; without, it returns false when the shard is full (QoS 0
// must never block the MQTT client).
func (p *Pipeline) Offer(m Message, wait bool) bool {
	ch := p.shards[shardOf(m.Topic, len(p.shards))]
	select {
	case <-p.stopped:
		return false
	default:
	}
	// Count first, so Idle never sees a queued message as nothing to do.
	p.metrics.queued.Add(1)
	if wait {
		select {
		case ch <- m:
			return true
		case <-p.stopped:
		}
	} else {
		select {
		case ch <- m:
			return true
		default:
			p.metrics.Dropped.WithLabelValues("queue_full").Inc()
		}
	}
	p.metrics.queued.Add(-1)
	return false
}

// shardOf hashes the device ID (devices/<id>/...), or the whole topic for
// anything else.
func shardOf(topic string, n int) int {
	key := topic
	if rest, ok := strings.CutPrefix(topic, "devices/"); ok {
		if id, _, found := strings.Cut(rest, "/"); found {
			key = id
		}
	}
	h := fnv.New32a()
	h.Write([]byte(key))
	return int(h.Sum32() % uint32(n))
}

func (p *Pipeline) work(ctx context.Context, ch chan Message) {
	batch := make([]Message, 0, p.batch)
	for {
		if ctx.Err() != nil {
			return // stopping: leave the queue alone, the broker sends it again
		}
		select {
		case <-ctx.Done():
			return
		case m := <-ch:
			batch = append(batch[:0], m)
		}
	fill:
		for len(batch) < p.batch {
			select {
			case m := <-ch:
				batch = append(batch, m)
			default:
				break fill
			}
		}
		p.busy.Add(1)
		p.metrics.queued.Add(-int64(len(batch)))
		p.metrics.BatchSize.Observe(float64(len(batch)))
		for _, m := range batch {
			if !p.deliver(ctx, m) {
				break // stopping: the rest stay unacknowledged and come back from the broker
			}
		}
		clear(batch) // drop payload references
		p.busy.Add(-1)
	}
}

// deliver forwards m, retrying as retry() allows, and acknowledges it when it
// is done. It returns false only when ctx ended first.
func (p *Pipeline) deliver(ctx context.Context, m Message) bool {
	for attempt := 1; ; attempt++ {
		err := p.fwd.Forward(ctx, m.Topic, m.Payload)
		if err != nil && ctx.Err() != nil {
			return false
		}
		p.metrics.IngestForward.WithLabelValues(resultLabel(err)).Inc()
		if err == nil {
			p.metrics.ForwardDelay.Observe(time.Since(m.Received).Seconds())
			p.ack(m)
			return true
		}
		if !retry(err, attempt, p.maxAttempts) {
			reason := "rejected"
			if attempt > 1 {
				reason = "retries_exhausted"
			}
			p.metrics.Dropped.WithLabelValues(reason).Inc()
			p.log.Warn("control-plane ingest failed; message dropped", "topic", m.Topic, "attempts", attempt, "error", err.Error())
			p.ack(m)
			return true
		}
		if attempt == 1 || attempt%10 == 0 {
			p.log.Warn("control-plane ingest failed; retrying", "topic", m.Topic, "attempt", attempt, "error", err.Error())
		}
		p.metrics.Retries.Inc()
		select {
		case <-ctx.Done():
			return false
		case <-time.After(p.backoff(attempt)):
		}
	}
}

func (p *Pipeline) ack(m Message) {
	if m.Ack != nil {
		m.Ack()
	}
}
