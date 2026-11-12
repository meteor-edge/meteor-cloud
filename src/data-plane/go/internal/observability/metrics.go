// Package observability holds Prometheus metrics and structured logging setup.
package observability

import (
	"io"
	"log/slog"
	"net/http"
	"strings"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Metrics is every metric the data plane exposes. Labels are bounded: never an
// organization, device, application or stream name.
type Metrics struct {
	Registry *prometheus.Registry

	MessagesReceived  *prometheus.CounterVec // source: http|mqtt
	MessagesRejected  *prometheus.CounterVec // source, reason
	MessagesProcessed *prometheus.CounterVec // result: stored|duplicate|failed
	IngestLatency     prometheus.Histogram
	QueueDepth        prometheus.Gauge
	QueueCapacity     prometheus.Gauge
	StorageWrite      prometheus.Histogram
	StorageErrors     *prometheus.CounterVec // op
	DeadLetters       *prometheus.CounterVec // reason

	LiveConnections   prometheus.Gauge
	LiveSubscriptions prometheus.Gauge
	LiveDelivered     prometheus.Counter
	LiveDropped       *prometheus.CounterVec // reason
	FailedDeliveries  *prometheus.CounterVec // kind: live|command

	BusConnected   prometheus.Gauge
	BusReconnects  prometheus.Counter
	MQTTAuth       *prometheus.CounterVec // action, result
	CommandsTotal  *prometheus.CounterVec // state
	CommandRetries prometheus.Counter
	DispatcherLag  prometheus.Gauge

	PolicySyncs       *prometheus.CounterVec // result
	PolicyCredentials prometheus.Gauge
	PolicyLastSuccess prometheus.Gauge

	HTTPRequests *prometheus.CounterVec   // route, method, status
	HTTPDuration *prometheus.HistogramVec // route
	RateLimited  prometheus.Counter
	AuthFailures *prometheus.CounterVec // reason
}

const ns = "meteorcloud_dataplane"

// NewMetrics registers all metrics on a fresh registry.
func NewMetrics() *Metrics {
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	f := func(c prometheus.Collector) { reg.MustRegister(c) }
	counter := func(name, help string) prometheus.Counter {
		c := prometheus.NewCounter(prometheus.CounterOpts{Namespace: ns, Name: name, Help: help})
		f(c)
		return c
	}
	counterVec := func(name, help string, labels ...string) *prometheus.CounterVec {
		c := prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: ns, Name: name, Help: help}, labels)
		f(c)
		return c
	}
	gauge := func(name, help string) prometheus.Gauge {
		g := prometheus.NewGauge(prometheus.GaugeOpts{Namespace: ns, Name: name, Help: help})
		f(g)
		return g
	}
	hist := func(name, help string, buckets []float64) prometheus.Histogram {
		h := prometheus.NewHistogram(prometheus.HistogramOpts{Namespace: ns, Name: name, Help: help, Buckets: buckets})
		f(h)
		return h
	}
	latency := []float64{.001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10}
	m := &Metrics{
		Registry:          reg,
		MessagesReceived:  counterVec("messages_received_total", "Messages received, by source.", "source"),
		MessagesRejected:  counterVec("messages_rejected_total", "Messages rejected before storage.", "source", "reason"),
		MessagesProcessed: counterVec("messages_processed_total", "Messages that left the pipeline.", "result"),
		IngestLatency:     hist("ingest_latency_seconds", "Time from receipt to durable storage.", latency),
		QueueDepth:        gauge("ingest_queue_depth", "Messages waiting for storage."),
		QueueCapacity:     gauge("ingest_queue_capacity", "Size of the bounded ingestion queue."),
		StorageWrite:      hist("storage_write_seconds", "Duration of one storage batch write.", latency),
		StorageErrors:     counterVec("storage_errors_total", "Storage operations that failed.", "op"),
		DeadLetters:       counterVec("dead_letters_total", "Messages written to the dead-letter table.", "reason"),
		LiveConnections:   gauge("live_connections", "Open application streaming connections."),
		LiveSubscriptions: gauge("live_subscriptions", "Active live subscriptions."),
		LiveDelivered:     counter("live_delivered_total", "Events delivered to live subscribers."),
		LiveDropped:       counterVec("live_dropped_connections_total", "Streaming connections closed by the server.", "reason"),
		FailedDeliveries:  counterVec("failed_deliveries_total", "Events or commands that could not be handed to the transport.", "kind"),
		BusConnected:      gauge("bus_connected", "1 while the message transport is connected."),
		BusReconnects:     counter("bus_reconnects_total", "Reconnections to the message transport."),
		MQTTAuth:          counterVec("mqtt_tokens_total", "Short-lived MQTT tokens issued to applications.", "action", "result"),
		CommandsTotal:     counterVec("commands_total", "Commands by state reached.", "state"),
		CommandRetries:    counter("command_redeliveries_total", "Commands dispatched again because they were not acknowledged."),
		DispatcherLag:     gauge("command_dispatcher_lag_seconds", "Age of the oldest due command the dispatcher picked up."),
		PolicySyncs:       counterVec("policy_syncs_total", "Policy snapshot syncs.", "result"),
		PolicyCredentials: gauge("policy_credentials", "Credentials in the loaded policy snapshot."),
		PolicyLastSuccess: gauge("policy_last_success_timestamp_seconds", "Unix time of the last successful policy sync."),
		HTTPRequests:      counterVec("http_requests_total", "HTTP requests.", "route", "method", "status"),
		RateLimited:       counter("rate_limited_total", "Requests rejected by the per-credential rate limit."),
		AuthFailures:      counterVec("auth_failures_total", "Rejected credentials.", "reason"),
	}
	m.HTTPDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{Namespace: ns, Name: "http_request_duration_seconds",
		Help: "HTTP request duration.", Buckets: latency}, []string{"route"})
	f(m.HTTPDuration)
	return m
}

// Handler serves the registry in the Prometheus text format.
func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{})
}

// PolicySync implements identity.SyncObserver.
func (m *Metrics) PolicySync(ok bool, credentials int) {
	if !ok {
		m.PolicySyncs.WithLabelValues("error").Inc()
		return
	}
	m.PolicySyncs.WithLabelValues("ok").Inc()
	m.PolicyLastSuccess.SetToCurrentTime()
	if credentials >= 0 {
		m.PolicyCredentials.Set(float64(credentials))
	}
}

// NewLogger builds the process logger. JSON by default; tokens and payloads are never
// passed to it by this service.
func NewLogger(w io.Writer, format, level string) *slog.Logger {
	var lvl slog.Level
	switch strings.ToLower(level) {
	case "debug":
		lvl = slog.LevelDebug
	case "warn", "warning":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	opts := &slog.HandlerOptions{Level: lvl}
	if strings.ToLower(format) == "console" || strings.ToLower(format) == "text" {
		return slog.New(slog.NewTextHandler(w, opts))
	}
	return slog.New(slog.NewJSONHandler(w, opts))
}
