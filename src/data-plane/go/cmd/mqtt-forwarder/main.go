// Command mqtt-forwarder takes device messages from the MQTT broker (shared
// subscription, so replicas split the load) and forwards each one to the
// control plane at POST /internal/mqtt/ingest. It also serves the HTTP API the
// control plane uses to publish and watch topics (contracts/mqtt-http.md).
//
// It replaces the Python data-plane service (src/data-plane/data_plane) and
// reads the same environment variables; see docs/data-plane/mqtt-forwarder.md.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"time"

	"meteorcloud/dataplane/internal/mqttfwd"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/service"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "mqtt-forwarder:", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := mqttfwd.LoadConfig()
	if err != nil {
		return err
	}
	log := observability.NewLogger(os.Stdout, cfg.LogFormat, cfg.LogLevel).With("service", "mqtt-forwarder")
	ctx, stop := service.SignalContext()
	defer stop()

	reg := service.NewRegistry()
	metrics := mqttfwd.NewMetrics(reg)
	api := &mqttfwd.API{Token: cfg.InternalToken, Metrics: metrics}
	if cfg.MetricsEnabled {
		api.Registry = reg
	}

	// Workers keep their own context so shutdown can let them finish the queue.
	workCtx, stopWork := context.WithCancel(context.Background())
	defer stopWork()
	ingest := mqttfwd.NewIngestClient(cfg.ControlPlaneURL, cfg.InternalToken, cfg.Workers, cfg.IngestTimeout)
	pipe := mqttfwd.NewPipeline(cfg.Workers, cfg.QueueSize, cfg.BatchSize, cfg.MaxAttempts, ingest, metrics, log)
	pipe.Start(workCtx)

	sessionDone := make(chan error, 1)
	if cfg.MQTTEnabled {
		sess := mqttfwd.NewSession(cfg, pipe, metrics, log)
		api.Broker = sess
		drain := func(limit time.Duration) {
			deadline := time.Now().Add(limit)
			for !pipe.Idle() && time.Now().Before(deadline) {
				time.Sleep(50 * time.Millisecond)
			}
			stopWork() // anything unfinished stays unacknowledged and is sent again
			pipe.Wait()
		}
		go func() { sessionDone <- sess.Run(ctx, drain) }()
	} else {
		log.Warn("MQTT_ENABLED=false: nothing is forwarded and /v1/* answers 503")
		go func() { <-ctx.Done(); sessionDone <- nil }()
	}

	srv := &http.Server{Addr: cfg.HTTPAddr, Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second}
	errs := make(chan error, 1)
	go service.Serve(srv, errs)
	log.Info("mqtt forwarder started", "http_addr", cfg.HTTPAddr, "broker", cfg.BrokerURL,
		"shared_group", cfg.SharedGroup, "workers", cfg.Workers, "queue_size", cfg.QueueSize, "mqtt_enabled", cfg.MQTTEnabled)

	select {
	case err = <-errs:
		stop()
		<-sessionDone
	case err = <-sessionDone:
	}
	sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if serr := srv.Shutdown(sctx); serr != nil && !errors.Is(serr, http.ErrServerClosed) {
		log.Debug("HTTP shutdown", "error", serr.Error())
	}
	log.Info("stopped")
	return err
}

// healthcheck is the container HEALTHCHECK: GET /health on HTTP_ADDR.
func healthcheck() int {
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8081"
	}
	if addr[0] == ':' {
		addr = "127.0.0.1" + addr
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + addr + "/health")
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck:", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, "healthcheck: status", resp.StatusCode)
		return 1
	}
	return 0
}
