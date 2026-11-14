// Package api is the HTTP surface of the data plane: the application-facing v1 API
// (contracts/data-plane-v1.md) and the internal API used by the control plane and
// the broker.
package api

import (
	"bufio"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"

	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/ingest"
	"meteorcloud/dataplane/internal/live"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

// Options are the limits and secrets the API enforces.
type Options struct {
	InternalToken    string
	Limits           envelope.Limits
	MaxBatch         int
	RateLimitPerSec  float64
	RateLimitBurst   int
	QueryMaxLimit    int
	QueryMaxBuckets  int
	QueryTimeout     time.Duration
	LifecycleEvents  bool
	AllowedWSOrigins []string
	// Direct MQTT access for applications: the secret shared with the broker's JWT
	// authenticator (empty disables it), the broker URL clients use, and token lifetime.
	MQTTJWTSecret string
	MQTTPublicURL string
	MQTTTokenTTL  time.Duration
	// Ready reports whether dependencies (storage, transport) are usable.
	Ready func(ctx context.Context) map[string]error
}

// Server wires the handlers to the services.
type Server struct {
	opts     Options
	policy   *identity.Store
	pipeline *ingest.Pipeline
	store    storage.Store
	hub      *live.Hub
	commands *commands.Service
	metrics  *observability.Metrics
	log      *slog.Logger

	limMu    sync.Mutex
	limiters map[string]*limiterEntry

	wsMu     sync.Mutex
	sessions map[*session]struct{}
	closing  bool
}

type limiterEntry struct {
	lim  *rate.Limiter
	seen time.Time
}

func NewServer(opts Options, policy *identity.Store, pipeline *ingest.Pipeline, store storage.Store, hub *live.Hub,
	cmds *commands.Service, metrics *observability.Metrics, log *slog.Logger) *Server {
	if opts.MaxBatch <= 0 {
		opts.MaxBatch = 500
	}
	if opts.QueryMaxLimit <= 0 {
		opts.QueryMaxLimit = 1000
	}
	if opts.QueryMaxBuckets <= 0 {
		opts.QueryMaxBuckets = 5000
	}
	if opts.QueryTimeout <= 0 {
		opts.QueryTimeout = 10 * time.Second
	}
	return &Server{opts: opts, policy: policy, pipeline: pipeline, store: store, hub: hub, commands: cmds,
		metrics: metrics, log: log, limiters: map[string]*limiterEntry{}, sessions: map[*session]struct{}{}}
}

// PublicHandler is what applications reach.
func (s *Server) PublicHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.Handle("GET /v1/whoami", s.auth("whoami", s.handleWhoami))
	mux.Handle("POST /v1/messages", s.auth("messages", s.handleMessages))
	mux.Handle("GET /v1/streams/{stream}/data", s.auth("stream_data", s.handleStreamData))
	mux.Handle("GET /v1/stream", s.auth("stream_ws", s.handleStream))
	mux.Handle("POST /v1/mqtt/credentials", s.auth("mqtt_credentials", s.handleMQTTCredentials))
	mux.Handle("POST /v1/commands", s.auth("commands_create", s.handleCommandCreate))
	mux.Handle("GET /v1/commands/pending", s.auth("commands_pending", s.handleCommandsPending))
	mux.Handle("GET /v1/commands/{id}", s.auth("commands_get", s.handleCommandGet))
	mux.Handle("POST /v1/commands/{id}/ack", s.auth("commands_ack", s.handleCommandAck))
	return mux
}

// OpsHandler carries metrics, probes and the internal API. Keep it off the internet.
func (s *Server) OpsHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.HandleFunc("GET /readyz", s.handleReady)
	mux.Handle("GET /metrics", s.metrics.Handler())
	mux.Handle("POST /internal/v1/commands", s.internal("internal_commands_create", s.handleInternalCommandCreate))
	mux.Handle("GET /internal/v1/organizations/{org}/commands/{id}", s.internal("internal_commands_get", s.handleInternalCommandGet))
	return mux
}

type apiError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]apiError{"error": {Code: code, Message: message}})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// Unwrap lets http.ResponseController reach the underlying connection.
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// Hijack lets the WebSocket upgrade take over the connection.
func (r *statusRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	hj, ok := r.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, errors.New("connection cannot be hijacked")
	}
	r.status = http.StatusSwitchingProtocols
	return hj.Hijack()
}

func (s *Server) observe(route string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()
		next(rec, r)
		s.metrics.HTTPRequests.WithLabelValues(route, r.Method, strconv.Itoa(rec.status)).Inc()
		if route != "stream_ws" {
			s.metrics.HTTPDuration.WithLabelValues(route).Observe(time.Since(start).Seconds())
		}
	}
}

type identityKey struct{}

func identityFrom(r *http.Request) *identity.Identity {
	return r.Context().Value(identityKey{}).(*identity.Identity)
}

func bearer(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if len(h) > 7 && strings.EqualFold(h[:7], "bearer ") {
		return strings.TrimSpace(h[7:])
	}
	return ""
}

// auth authenticates the application credential and applies its rate limit. The
// identity it puts in the context is the only source of organization, application
// and device ids for the request.
func (s *Server) auth(route string, next http.HandlerFunc) http.Handler {
	return s.observe(route, func(w http.ResponseWriter, r *http.Request) {
		token := bearer(r)
		if token == "" {
			s.metrics.AuthFailures.WithLabelValues("missing").Inc()
			w.Header().Set("WWW-Authenticate", "Bearer")
			writeError(w, http.StatusUnauthorized, "missing_credentials", "Send the application token as a Bearer token.")
			return
		}
		id, err := s.policy.Authenticate(token)
		if errors.Is(err, identity.ErrPolicyStale) {
			s.metrics.AuthFailures.WithLabelValues("policy_unavailable").Inc()
			w.Header().Set("Retry-After", "5")
			writeError(w, http.StatusServiceUnavailable, "policy_unavailable", "The data plane has no current policy yet. Retry shortly.")
			return
		}
		if err != nil {
			s.metrics.AuthFailures.WithLabelValues("invalid").Inc()
			w.Header().Set("WWW-Authenticate", "Bearer")
			writeError(w, http.StatusUnauthorized, "invalid_credentials", "The application token is invalid, expired or revoked.")
			return
		}
		if !s.allow(id.CredentialID, 1) {
			s.rateLimited(w)
			return
		}
		next(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
	})
}

func (s *Server) rateLimited(w http.ResponseWriter) {
	s.metrics.RateLimited.Inc()
	w.Header().Set("Retry-After", "1")
	writeError(w, http.StatusTooManyRequests, "rate_limited", "Too many requests for this credential. Slow down and retry.")
}

// allow spends n tokens of the credential's bucket.
func (s *Server) allow(credentialID string, n int) bool {
	if s.opts.RateLimitPerSec <= 0 {
		return true
	}
	now := time.Now()
	s.limMu.Lock()
	entry, ok := s.limiters[credentialID]
	if !ok {
		burst := s.opts.RateLimitBurst
		if burst < 1 {
			burst = int(s.opts.RateLimitPerSec)
		}
		entry = &limiterEntry{lim: rate.NewLimiter(rate.Limit(s.opts.RateLimitPerSec), burst), seen: now}
		s.limiters[credentialID] = entry
		if len(s.limiters) > 50000 { // bound memory: forget idle credentials
			for k, e := range s.limiters {
				if now.Sub(e.seen) > 10*time.Minute {
					delete(s.limiters, k)
				}
			}
		}
	}
	entry.seen = now
	s.limMu.Unlock()
	// A request larger than the bucket takes the whole bucket instead of failing forever.
	if b := entry.lim.Burst(); n > b && b > 0 {
		n = b
	}
	return entry.lim.AllowN(now, n)
}

func (s *Server) internal(route string, next http.HandlerFunc) http.Handler {
	return s.observe(route, func(w http.ResponseWriter, r *http.Request) {
		got := r.Header.Get("X-Data-Plane-Internal-Token")
		if s.opts.InternalToken == "" || subtle.ConstantTimeCompare([]byte(got), []byte(s.opts.InternalToken)) != 1 {
			writeError(w, http.StatusUnauthorized, "internal_forbidden", "Invalid internal token.")
			return
		}
		next(w, r)
	})
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	checks := map[string]string{"policy": "ok"}
	ready := true
	if !s.policy.Ready() {
		checks["policy"] = "no usable policy snapshot"
		ready = false
	}
	if s.opts.Ready != nil {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		for name, err := range s.opts.Ready(ctx) {
			if err != nil {
				checks[name] = err.Error()
				ready = false
			} else {
				checks[name] = "ok"
			}
		}
	}
	status := http.StatusOK
	if !ready {
		status = http.StatusServiceUnavailable
	}
	writeJSON(w, status, map[string]any{"ready": ready, "checks": checks})
}

func (s *Server) handleWhoami(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	writeJSON(w, http.StatusOK, identityView(id))
}

func identityView(id *identity.Identity) map[string]any {
	view := map[string]any{
		"organization_id": id.OrganizationID,
		"application_id":  id.ApplicationID,
		"application":     id.Application,
		"environment":     id.Environment,
		"credential_id":   id.CredentialID,
		"expires_at":      id.ExpiresAt.UTC().Format(time.RFC3339),
		"permissions":     id.Permissions,
	}
	if id.DeviceID != "" {
		view["device_id"] = id.DeviceID
	} else {
		view["device_id"] = nil
	}
	return view
}

// decodeBody reads a bounded JSON body.
func decodeBody(w http.ResponseWriter, r *http.Request, limit int64, dst any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(dst); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, http.StatusRequestEntityTooLarge, "body_too_large", "The request body is too large.")
		} else {
			writeError(w, http.StatusBadRequest, "invalid_json", "The request body is not valid JSON for this endpoint.")
		}
		return false
	}
	return true
}
