package natsjs

import (
	"github.com/prometheus/client_golang/prometheus"
)

// Metrics covers the connection, publishing and consuming. Labels are bounded:
// a consumer name, a subject root ("telemetry.v1") or a result, never an
// organization, device or event id.
type Metrics struct {
	Connected   prometheus.Gauge
	Reconnects  prometheus.Counter
	Disconnects prometheus.Counter
	AsyncErrors prometheus.Counter

	Published        *prometheus.CounterVec   // subject_root, result: ok|duplicate|error
	PublishDuration  prometheus.Histogram
	Consumed         *prometheus.CounterVec   // consumer
	Processed        *prometheus.CounterVec   // consumer, result: ack|nak|term
	Redeliveries     *prometheus.CounterVec   // consumer: messages seen with delivery count > 1
	ProcessingTime   *prometheus.HistogramVec // consumer
	BatchSize        *prometheus.HistogramVec // consumer
	Pending          *prometheus.GaugeVec     // consumer: messages not yet delivered
	AckPending       *prometheus.GaugeVec     // consumer: delivered, not yet acknowledged
	Redelivered      *prometheus.GaugeVec     // consumer: currently redelivered, unacknowledged
	EndToEndLatency  *prometheus.HistogramVec // consumer: stream timestamp to acknowledgement
}

const ns = "meteorcloud_nats"

// NewMetrics registers the NATS metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Connected:   prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Name: "connected", Help: "1 while connected to a NATS server."}),
		Reconnects:  prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "reconnects_total", Help: "Successful reconnections."}),
		Disconnects: prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "disconnects_total", Help: "Lost connections."}),
		AsyncErrors: prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: "async_errors_total", Help: "Asynchronous errors reported by the NATS client (e.g. permission violations)."}),
		Published: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "published_total",
			Help: "JetStream publishes by result: ok, duplicate (rejected by Nats-Msg-Id) or error."}, []string{"subject_root", "result"}),
		PublishDuration: prometheus.NewHistogram(prometheus.HistogramOpts{Namespace: ns, Name: "publish_duration_seconds",
			Help: "Time until the stream acknowledged a publish.", Buckets: prometheus.ExponentialBuckets(0.0005, 2, 14)}),
		Consumed: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "consumed_total",
			Help: "Messages delivered to a consumer."}, []string{"consumer"}),
		Processed: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "processed_total",
			Help: "Messages finished by a consumer: ack, nak (retry later) or term (never redeliver)."}, []string{"consumer", "result"}),
		Redeliveries: prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: "redeliveries_total",
			Help: "Messages received again after a failure or timeout (retries)."}, []string{"consumer"}),
		ProcessingTime: prometheus.NewHistogramVec(prometheus.HistogramOpts{Namespace: ns, Name: "processing_duration_seconds",
			Help: "Time a consumer spent on one message or batch.", Buckets: prometheus.ExponentialBuckets(0.0005, 2, 16)}, []string{"consumer"}),
		BatchSize: prometheus.NewHistogramVec(prometheus.HistogramOpts{Namespace: ns, Name: "batch_size",
			Help: "Messages per processed batch.", Buckets: []float64{1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000}}, []string{"consumer"}),
		Pending: prometheus.NewGaugeVec(prometheus.GaugeOpts{Namespace: ns, Name: "consumer_pending_messages",
			Help: "Messages in the stream not yet delivered to the consumer."}, []string{"consumer"}),
		AckPending: prometheus.NewGaugeVec(prometheus.GaugeOpts{Namespace: ns, Name: "consumer_ack_pending_messages",
			Help: "Messages delivered but not yet acknowledged."}, []string{"consumer"}),
		Redelivered: prometheus.NewGaugeVec(prometheus.GaugeOpts{Namespace: ns, Name: "consumer_redelivered_messages",
			Help: "Messages currently being redelivered."}, []string{"consumer"}),
		EndToEndLatency: prometheus.NewHistogramVec(prometheus.HistogramOpts{Namespace: ns, Name: "end_to_end_latency_seconds",
			Help: "From the stream storing a message to the consumer acknowledging it.", Buckets: prometheus.ExponentialBuckets(0.001, 2, 18)}, []string{"consumer"}),
	}
	reg.MustRegister(m.Connected, m.Reconnects, m.Disconnects, m.AsyncErrors, m.Published, m.PublishDuration,
		m.Consumed, m.Processed, m.Redeliveries, m.ProcessingTime, m.BatchSize, m.Pending, m.AckPending,
		m.Redelivered, m.EndToEndLatency)
	return m
}
