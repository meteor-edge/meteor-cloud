package natsjs

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"

	"meteorcloud/dataplane/internal/envelope"
)

// Headers set on every published event, so consumers and operators can route and
// inspect messages without decoding the body.
const (
	HeaderOrganization  = "Meteor-Organization-Id"
	HeaderDevice        = "Meteor-Device-Id"
	HeaderSchemaVersion = "Meteor-Schema-Version"
)

// Publisher writes events to JetStream and waits for the stream's acknowledgement.
type Publisher struct {
	JS      jetstream.JetStream
	Stream  string // expected stream; a misrouted subject fails instead of landing elsewhere
	Metrics *Metrics
}

// subjectRoot is the bounded metric label for a subject: "telemetry.v1" or "device.status.v1".
func subjectRoot(subject string) string {
	parts := strings.Split(subject, ".")
	for i, p := range parts {
		if len(p) > 1 && p[0] == 'v' && p[1] >= '0' && p[1] <= '9' {
			return strings.Join(parts[:i+1], ".")
		}
	}
	return "other"
}

// Publish stores ev on subject. The event id is the Nats-Msg-Id, so a publish
// retried within the stream's duplicate window is stored once. It returns
// duplicate=true when the stream already had the id.
func (p *Publisher) Publish(ctx context.Context, subject string, ev *envelope.Event) (duplicate bool, err error) {
	body, err := json.Marshal(ev)
	if err != nil {
		return false, err
	}
	msg := &nats.Msg{Subject: subject, Data: body, Header: nats.Header{}}
	msg.Header.Set(HeaderOrganization, ev.OrganizationID)
	msg.Header.Set(HeaderDevice, ev.DeviceID)
	msg.Header.Set(HeaderSchemaVersion, "1")
	opts := []jetstream.PublishOpt{jetstream.WithMsgID(ev.ID)}
	if p.Stream != "" {
		opts = append(opts, jetstream.WithExpectStream(p.Stream))
	}
	start := time.Now()
	ack, err := p.JS.PublishMsg(ctx, msg, opts...)
	p.Metrics.PublishDuration.Observe(time.Since(start).Seconds())
	root := subjectRoot(subject)
	if err != nil {
		p.Metrics.Published.WithLabelValues(root, "error").Inc()
		return false, err
	}
	if ack.Duplicate {
		p.Metrics.Published.WithLabelValues(root, "duplicate").Inc()
		return true, nil
	}
	p.Metrics.Published.WithLabelValues(root, "ok").Inc()
	return false, nil
}

// DecodeEvent reads a published event. A message that does not decode, or whose
// identity disagrees with its subject, can never succeed and should be terminated.
func DecodeEvent(msg jetstream.Msg) (*envelope.Event, error) {
	var ev envelope.Event
	if err := json.Unmarshal(msg.Data(), &ev); err != nil {
		return nil, err
	}
	if ev.ID == "" || ev.OrganizationID == "" || ev.SchemaVersion < 1 {
		return nil, errors.New("event is missing id, organization_id or schema_version")
	}
	// The subject carries <org>.<device> as its last two tokens; they were set by
	// ingestion from verified identity and must match the body.
	parts := strings.Split(msg.Subject(), ".")
	if len(parts) >= 2 && (parts[len(parts)-2] != ev.OrganizationID || parts[len(parts)-1] != ev.DeviceID) {
		return nil, errors.New("event identity does not match its subject")
	}
	return &ev, nil
}

// RetryDelay picks the redelivery delay for a failed message from a backoff
// schedule by its delivery count (1 for the first delivery).
func RetryDelay(numDelivered uint64, schedule []time.Duration) time.Duration {
	if len(schedule) == 0 {
		return time.Second
	}
	i := int(numDelivered) - 1
	if i < 0 {
		i = 0
	}
	if i >= len(schedule) {
		i = len(schedule) - 1
	}
	return schedule[i]
}

// Batches pulls bounded batches from a durable consumer and hands each one to
// handle, waiting for it to return before pulling again. That wait is the
// backpressure: a slow handler simply pulls less, and at most FetchBatch messages
// per replica (and MaxAckPending in total) are outstanding. handle must ack, nak
// or term every message it gets. Batches returns when ctx ends, after the batch in
// progress has been handled, so shutdown never abandons half-processed messages.
func Batches(ctx context.Context, cons jetstream.Consumer, name string, batch int, maxWait time.Duration,
	m *Metrics, log *slog.Logger, handle func(msgs []jetstream.Msg)) {
	for ctx.Err() == nil {
		res, err := cons.Fetch(batch, jetstream.FetchMaxWait(maxWait))
		if err != nil {
			log.Warn("fetch failed", "consumer", name, "error", err.Error())
			sleep(ctx, time.Second)
			continue
		}
		msgs := make([]jetstream.Msg, 0, batch)
		for msg := range res.Messages() {
			msgs = append(msgs, msg)
		}
		if err := res.Error(); err != nil && !errors.Is(err, nats.ErrTimeout) && !errors.Is(err, jetstream.ErrNoHeartbeat) {
			log.Warn("fetch ended with an error", "consumer", name, "error", err.Error())
			if len(msgs) == 0 {
				sleep(ctx, time.Second)
			}
		}
		if len(msgs) == 0 {
			continue
		}
		Observe(msgs, name, m)
		handle(msgs)
	}
}

// Observe records delivery metrics for a batch.
func Observe(msgs []jetstream.Msg, name string, m *Metrics) {
	m.Consumed.WithLabelValues(name).Add(float64(len(msgs)))
	for _, msg := range msgs {
		if meta, err := msg.Metadata(); err == nil && meta.NumDelivered > 1 {
			m.Redeliveries.WithLabelValues(name).Inc()
		}
	}
}

// Acked records an acknowledged message and its end-to-end latency.
func Acked(msg jetstream.Msg, name string, m *Metrics) {
	m.Processed.WithLabelValues(name, "ack").Inc()
	if meta, err := msg.Metadata(); err == nil {
		m.EndToEndLatency.WithLabelValues(name).Observe(time.Since(meta.Timestamp).Seconds())
	}
}

func sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}
