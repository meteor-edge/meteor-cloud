package deviceingest

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/envelope"
)

// Directory resolves a device to its organization.
type Directory interface {
	Organization(deviceID string) (orgID string, ok bool, err error)
}

// Publisher stores an event on a subject (natsjs.Publisher).
type Publisher interface {
	Publish(ctx context.Context, subject string, ev *envelope.Event) (duplicate bool, err error)
}

// Metrics of the ingestion service. Labels are bounded.
type Metrics struct {
	Received      *prometheus.CounterVec // channel
	Rejected      *prometheus.CounterVec // reason
	Published     *prometheus.CounterVec // channel, result: stored|duplicate
	TimeReplaced  prometheus.Counter
	PublishRetry  prometheus.Counter
	HandleSeconds prometheus.Histogram
	MQTTConnected prometheus.Gauge
	DirSyncs      *prometheus.CounterVec // result
	DirDevices    prometheus.Gauge
}

// NewMetrics registers the ingestion metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	const ns = "meteorcloud_device_ingest"
	m := &Metrics{
		Received: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "received_total",
			Help: "MQTT messages received from devices."}, []string{"channel"}),
		Rejected: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "rejected_total",
			Help: "Device messages refused (never published) by reason."}, []string{"reason"}),
		Published: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "published_total",
			Help: "Device events stored on JetStream (duplicate: already stored under the same id)."}, []string{"channel", "result"}),
		TimeReplaced: prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "timestamp_replaced_total",
			Help: "Events whose device timestamp was outside the accepted window and replaced by the receive time."}),
		PublishRetry: prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "publish_retries_total",
			Help: "JetStream publish attempts repeated after a failure (the MQTT message stays unacknowledged)."}),
		HandleSeconds: prometheus.NewHistogram(prometheus.HistogramOpts{Namespace: ns, Name: "handle_duration_seconds",
			Help: "From MQTT receive to JetStream acknowledgement.", Buckets: prometheus.ExponentialBuckets(0.0005, 2, 16)}),
		MQTTConnected: prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Name: "mqtt_connected", Help: "1 while subscribed to the broker."}),
		DirSyncs: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "directory_syncs_total",
			Help: "Device directory refreshes by result."}, []string{"result"}),
		DirDevices: prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Name: "directory_devices", Help: "Devices in the loaded directory."}),
	}
	reg.MustRegister(m.Received, m.Rejected, m.Published, m.TimeReplaced, m.PublishRetry, m.HandleSeconds,
		m.MQTTConnected, m.DirSyncs, m.DirDevices)
	return m
}

// Ingester handles one device message at a time; run several in parallel.
type Ingester struct {
	Dir       Directory
	Publisher Publisher
	Metrics   *Metrics
	Log       *slog.Logger
	Limits    envelope.Limits
	// MaxBackoff caps the wait between publish retries.
	MaxBackoff time.Duration
	now        func() time.Time
}

// Handle processes one MQTT message and reports whether the broker may be
// acknowledged. It returns true once the event is stored on JetStream (or was a
// duplicate), and also for messages that can never be accepted (they are counted
// and logged, without their payload, then dropped: MQTT has no error channel back
// to a device). While JetStream or the device directory is unavailable it keeps
// retrying with backoff and does not return, so the message stays unacknowledged:
// that holds the broker's in-flight window (MQTT Receive Maximum) and is the
// backpressure towards the broker. It returns false only when ctx ends; the broker
// then redelivers the message to another ingestion replica or after a restart.
func (in *Ingester) Handle(ctx context.Context, topic string, payload []byte) bool {
	start := time.Now()
	deviceID, ch, ok := ParseTopic(topic)
	if !ok {
		in.Metrics.Rejected.WithLabelValues(ReasonInvalidTopic).Inc()
		in.Log.Warn("device message rejected", "reason", ReasonInvalidTopic, "topic_levels", topicShape(topic))
		return true
	}
	in.Metrics.Received.WithLabelValues(string(ch)).Inc()

	backoff := 100 * time.Millisecond
	maxBackoff := in.MaxBackoff
	if maxBackoff <= 0 {
		maxBackoff = 5 * time.Second
	}
	wait := func() bool {
		in.Metrics.PublishRetry.Inc()
		select {
		case <-ctx.Done():
			return false
		case <-time.After(backoff):
		}
		if backoff < maxBackoff {
			backoff = min(backoff*2, maxBackoff)
		}
		return true
	}

	var orgID string
	for {
		org, known, err := in.Dir.Organization(deviceID)
		if err == nil {
			if !known {
				in.Metrics.Rejected.WithLabelValues(ReasonUnknownDevice).Inc()
				in.Log.Warn("device message rejected", "reason", ReasonUnknownDevice, "device_id", deviceID, "channel", string(ch))
				return true
			}
			orgID = org
			break
		}
		// Fail closed, but keep the message: the directory comes back with the control plane.
		in.Log.Warn("device directory unavailable; holding message", "device_id", deviceID, "error", err.Error())
		if !wait() {
			return false
		}
	}

	now := time.Now
	if in.now != nil {
		now = in.now
	}
	res, rej := Normalize(deviceID, orgID, ch, payload, now(), in.Limits)
	if rej != nil {
		in.Metrics.Rejected.WithLabelValues(rej.Code).Inc()
		in.Log.Warn("device message rejected", "reason", rej.Code, "detail", rej.Message,
			"organization_id", orgID, "device_id", deviceID, "channel", string(ch), "payload_bytes", len(payload))
		return true
	}
	if res.TimeReplaced {
		in.Metrics.TimeReplaced.Inc()
	}
	ev := res.Event
	subject := Subject(ch, orgID, deviceID)
	for {
		pctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		dup, err := in.Publisher.Publish(pctx, subject, ev)
		cancel()
		if err == nil {
			result := "stored"
			if dup {
				result = "duplicate"
			}
			in.Metrics.Published.WithLabelValues(string(ch), result).Inc()
			in.Metrics.HandleSeconds.Observe(time.Since(start).Seconds())
			return true
		}
		if ctx.Err() != nil || errors.Is(err, context.Canceled) {
			return false
		}
		in.Log.Warn("JetStream publish failed; retrying (MQTT message not acknowledged)",
			"organization_id", orgID, "device_id", deviceID, "event_id", ev.ID, "error", err.Error())
		if !wait() {
			return false
		}
	}
}

// topicShape logs the structure of an unexpected topic without its values.
func topicShape(topic string) int {
	n := 1
	for i := 0; i < len(topic); i++ {
		if topic[i] == '/' {
			n++
		}
	}
	return n
}
