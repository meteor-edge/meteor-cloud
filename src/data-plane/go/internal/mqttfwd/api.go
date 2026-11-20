package mqttfwd

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Broker is what the HTTP API needs from the MQTT session.
type Broker interface {
	Publish(ctx context.Context, topic, payload string, qos byte, retain bool) error
	Watch(ctx context.Context, topic string) error
	Unwatch(ctx context.Context, topic string) error
}

// API serves the control plane's side of contracts/mqtt-http.md on HTTP_ADDR:
// GET /health, POST /v1/publish, POST /v1/subscriptions, POST
// /v1/subscriptions/unwatch, and GET /metrics. Status codes and error bodies
// follow the Python (FastAPI) service.
type API struct {
	Token    string
	Broker   Broker // nil when MQTT_ENABLED=false
	Metrics  *Metrics
	Registry *prometheus.Registry // nil hides /metrics (METRICS_ENABLED=false)
}

// Handler returns the routed, instrumented handler.
func (a *API) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	if a.Registry != nil {
		mux.Handle("GET /metrics", promhttp.HandlerFor(a.Registry, promhttp.HandlerOpts{Registry: a.Registry}))
	}
	mux.HandleFunc("POST /v1/publish", a.publish)
	mux.HandleFunc("POST /v1/subscriptions", a.subscription(func(b Broker) func(context.Context, string) error { return b.Watch }))
	mux.HandleFunc("POST /v1/subscriptions/unwatch", a.subscription(func(b Broker) func(context.Context, string) error { return b.Unwatch }))
	return a.instrument(mux)
}

type publishRequest struct {
	Topic   *string `json:"topic"`
	Payload *string `json:"payload"`
	QoS     *int    `json:"qos"`
	Retain  *bool   `json:"retain"`
}

type subscriptionRequest struct {
	Topic *string `json:"topic"`
}

func (a *API) authorized(w http.ResponseWriter, r *http.Request) bool {
	got := r.Header.Get("X-MQTT-Internal-Token")
	if a.Token == "" || subtle.ConstantTimeCompare([]byte(got), []byte(a.Token)) != 1 {
		writeDetail(w, http.StatusUnauthorized, "Invalid MQTT internal token.")
		return false
	}
	return true
}

func (a *API) publish(w http.ResponseWriter, r *http.Request) {
	var req publishRequest
	if !decode(w, r, &req) {
		return
	}
	if req.Topic == nil || req.Payload == nil {
		writeDetail(w, http.StatusUnprocessableEntity, "topic and payload are required")
		return
	}
	qos, retain := 1, false
	if req.QoS != nil {
		qos = *req.QoS
	}
	if req.Retain != nil {
		retain = *req.Retain
	}
	if qos < 0 || qos > 2 {
		writeDetail(w, http.StatusUnprocessableEntity, "qos must be 0, 1 or 2")
		return
	}
	if !a.authorized(w, r) {
		return
	}
	err := ErrNotRunning
	if a.Broker != nil {
		err = a.Broker.Publish(r.Context(), *req.Topic, *req.Payload, byte(qos), retain)
	}
	if err != nil {
		a.Metrics.Publish.WithLabelValues("error").Inc()
		writeDetail(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	a.Metrics.Publish.WithLabelValues("ok").Inc()
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

func (a *API) subscription(op func(Broker) func(context.Context, string) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req subscriptionRequest
		if !decode(w, r, &req) {
			return
		}
		if req.Topic == nil {
			writeDetail(w, http.StatusUnprocessableEntity, "topic is required")
			return
		}
		if !a.authorized(w, r) {
			return
		}
		err := ErrNotRunning
		if a.Broker != nil {
			err = op(a.Broker)(r.Context(), *req.Topic)
		}
		if err != nil {
			writeDetail(w, http.StatusServiceUnavailable, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	}
}

// decode reads a body that must be exactly one JSON object. Like FastAPI, a
// malformed body is a 422 before the token is checked.
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	err := dec.Decode(v)
	if err == nil {
		if _, extra := dec.Token(); extra != io.EOF {
			err = errors.New("unexpected data after the JSON object")
		}
	}
	if err != nil {
		writeDetail(w, http.StatusUnprocessableEntity, "request body must be one JSON object: "+err.Error())
		return false
	}
	return true
}

func writeDetail(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]string{"detail": detail})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// instrument records http_requests_total and http_request_duration_seconds
// with the route as the path label, skipping /health and /metrics.
func (a *API) instrument(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" || r.URL.Path == "/metrics" {
			mux.ServeHTTP(w, r)
			return
		}
		path := "<unmatched>"
		if _, pattern := mux.Handler(r); pattern != "" {
			// The registered route ("POST /v1/publish" -> "/v1/publish"), never the
			// request path: a redirect to the clean path also matches a pattern, and
			// a request-chosen label would grow the metric without bound.
			_, path, _ = strings.Cut(pattern, " ")
		}
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()
		defer func() {
			a.Metrics.HTTPRequests.WithLabelValues(r.Method, path, strconv.Itoa(rec.status)).Inc()
			a.Metrics.HTTPDuration.WithLabelValues(r.Method, path).Observe(time.Since(start).Seconds())
		}()
		mux.ServeHTTP(rec, r)
	})
}
