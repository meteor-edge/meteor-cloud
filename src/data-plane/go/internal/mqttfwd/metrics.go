package mqttfwd

import (
	"strings"
	"sync/atomic"

	"github.com/prometheus/client_golang/prometheus"
)

// Metrics keeps the names and labels of the Python service, so dashboards and
// alerts (deploy/compose/config/observability) work unchanged, and adds a few
// for the queue. Topics and device IDs are never labels.
type Metrics struct {
	MQTTConnected    prometheus.Gauge
	MessagesReceived *prometheus.CounterVec // kind: status|metrics|command_result|other
	IngestForward    *prometheus.CounterVec // result: ok|http_error|unreachable (one per attempt)
	Publish          *prometheus.CounterVec // result: ok|error
	HTTPRequests     *prometheus.CounterVec // method, path, status
	HTTPDuration     *prometheus.HistogramVec

	QueueCapacity prometheus.Gauge
	Dropped       *prometheus.CounterVec // reason: queue_full|rejected|retries_exhausted
	Retries       prometheus.Counter
	BatchSize     prometheus.Histogram
	ForwardDelay  prometheus.Histogram

	// queued backs meteorcloud_dataplane_forward_queue_depth. It is read at
	// scrape time, so concurrent updates can never publish a stale value.
	queued atomic.Int64
}

// NewMetrics registers the forwarder metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		MQTTConnected: prometheus.NewGauge(prometheus.GaugeOpts{Name: "meteorcloud_dataplane_mqtt_connected",
			Help: "1 when the platform MQTT client is connected to the broker"}),
		MessagesReceived: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "meteorcloud_dataplane_mqtt_messages_received_total",
			Help: "Device MQTT messages received"}, []string{"kind"}),
		IngestForward: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "meteorcloud_dataplane_ingest_forward_total",
			Help: "Messages forwarded to the control-plane ingest API"}, []string{"result"}),
		Publish: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "meteorcloud_dataplane_publish_total",
			Help: "Publishes requested by the control plane"}, []string{"result"}),
		HTTPRequests: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "http_requests_total",
			Help: "HTTP requests"}, []string{"method", "path", "status"}),
		HTTPDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "http_request_duration_seconds",
			Help: "HTTP request duration in seconds"}, []string{"method", "path"}),

		QueueCapacity: prometheus.NewGauge(prometheus.GaugeOpts{Name: "meteorcloud_dataplane_forward_queue_capacity",
			Help: "Most device messages held before the broker is slowed down"}),
		Dropped: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "meteorcloud_dataplane_forward_dropped_total",
			Help: "Device messages given up on, by reason"}, []string{"reason"}),
		Retries: prometheus.NewCounter(prometheus.CounterOpts{Name: "meteorcloud_dataplane_forward_retries_total",
			Help: "Ingest attempts repeated after a failure"}),
		BatchSize: prometheus.NewHistogram(prometheus.HistogramOpts{Name: "meteorcloud_dataplane_forward_batch_size",
			Help: "Messages a shard took from its queue at once", Buckets: []float64{1, 2, 4, 8, 16, 32, 64, 128}}),
		ForwardDelay: prometheus.NewHistogram(prometheus.HistogramOpts{Name: "meteorcloud_dataplane_forward_delay_seconds",
			Help: "From MQTT receive to control-plane acceptance", Buckets: prometheus.ExponentialBuckets(0.001, 2, 16)}),
	}
	depth := prometheus.NewGaugeFunc(prometheus.GaugeOpts{Name: "meteorcloud_dataplane_forward_queue_depth",
		Help: "Device messages waiting to be forwarded"}, func() float64 { return float64(m.queued.Load()) })
	reg.MustRegister(m.MQTTConnected, m.MessagesReceived, m.IngestForward, m.Publish, m.HTTPRequests, m.HTTPDuration,
		depth, m.QueueCapacity, m.Dropped, m.Retries, m.BatchSize, m.ForwardDelay)
	return m
}

// messageKind is the "kind" label of a device topic, as in the Python service.
func messageKind(topic string) string {
	parts := strings.SplitN(topic, "/", 3)
	if len(parts) < 3 || parts[0] != "devices" {
		return "other"
	}
	switch parts[2] {
	case "status":
		return "status"
	case "metrics":
		return "metrics"
	case "commands/result":
		return "command_result"
	}
	return "other"
}
