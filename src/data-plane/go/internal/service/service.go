// Package service holds the small amount of process plumbing every data-plane
// worker shares: a private ops listener (metrics, health, readiness), the
// container health check and signal handling.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// NewRegistry returns a registry with Go runtime and process metrics.
func NewRegistry() *prometheus.Registry {
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	return reg
}

// Ops serves GET /metrics, /healthz and /readyz on addr. It must stay on a
// private network: it is for Prometheus and the orchestrator, not for clients.
func Ops(addr string, reg *prometheus.Registry, ready func(context.Context) map[string]error) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{Registry: reg}))
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		checks := map[string]string{}
		status := http.StatusOK
		for name, err := range ready(ctx) {
			if err != nil {
				checks[name] = err.Error()
				status = http.StatusServiceUnavailable
			} else {
				checks[name] = "ok"
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]any{"ready": status == http.StatusOK, "checks": checks})
	})
	return &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
}

// Serve runs s until it is shut down.
func Serve(s *http.Server, errs chan<- error) {
	if err := s.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		errs <- fmt.Errorf("listen %s: %w", s.Addr, err)
	}
}

// Healthcheck asks the local ops listener (address in env key, default def)
// whether the process is ready; it is the container HEALTHCHECK.
func Healthcheck(key, def string) int {
	addr := os.Getenv(key)
	if addr == "" {
		addr = def
	}
	if addr[0] == ':' {
		addr = "127.0.0.1" + addr
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + addr + "/readyz")
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck:", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, "healthcheck: not ready, status", resp.StatusCode)
		return 1
	}
	return 0
}

// SignalContext ends on SIGINT or SIGTERM.
func SignalContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
}
