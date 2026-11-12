// Command dataplane is the MeteorCloud application data plane: ingestion, live
// distribution, historical queries and command delivery in one process.
//
// One binary keeps operations simple. The roles are separate packages behind
// interfaces, so they can become separate deployments if scale ever requires it.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"meteorcloud/dataplane/internal/api"
	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/config"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/ingest"
	"meteorcloud/dataplane/internal/live"
	"meteorcloud/dataplane/internal/mqttgw"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "dataplane:", err)
		os.Exit(1)
	}
}

// healthcheck asks the local ops listener whether this instance is ready. It is the
// container HEALTHCHECK, so the image needs no curl or wget.
func healthcheck() int {
	addr := os.Getenv("DATAPLANE_OPS_ADDR")
	if addr == "" {
		addr = ":8092"
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

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	log := observability.NewLogger(os.Stdout, cfg.LogFormat, cfg.LogLevel)
	metrics := observability.NewMetrics()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Policy: applications, permissions and credential hashes from the control plane.
	policy := identity.NewStore(cfg.PolicyMaxStale)
	var source identity.Source
	if cfg.PolicyFile != "" {
		source = &identity.FileSource{Path: cfg.PolicyFile}
		log.Warn("policy comes from a file; use the control plane in production", "path", cfg.PolicyFile)
	} else {
		source = &identity.HTTPSource{URL: cfg.ControlPlaneURL, Token: cfg.InternalToken,
			Client: &http.Client{Timeout: 15 * time.Second}}
	}
	syncer := &identity.Syncer{Store: policy, Source: source, Interval: cfg.PolicyInterval, Log: log, Observer: metrics}
	go syncer.Run(ctx)

	// Storage.
	var store storage.Store
	switch cfg.StorageBackend {
	case "memory":
		store = storage.NewMemory()
		log.Warn("in-memory storage: data is lost on restart")
	default:
		pg, err := storage.OpenPostgres(ctx, cfg.DatabaseURL, int32(cfg.DatabaseConns), cfg.QueryTimeout)
		if err != nil {
			return fmt.Errorf("storage: %w", err)
		}
		store = pg
		if cfg.StorageBackend == "clickhouse" {
			ch, err := storage.OpenClickHouse(ctx, storage.ClickHouseOptions{
				URL: cfg.ClickHouseURL, Database: cfg.ClickHouseDatabase, User: cfg.ClickHouseUser,
				Password: cfg.ClickHousePassword, AsyncInsert: cfg.ClickHouseAsyncMode,
				QueryTimeout: cfg.QueryTimeout, RetentionDays: cfg.RetentionDays,
			})
			if err != nil {
				pg.Close()
				return fmt.Errorf("storage: %w", err)
			}
			store = &storage.Split{Store: pg, Events: ch}
		}
	}
	defer store.Close()

	limits := envelope.Limits{MaxPayloadBytes: cfg.MaxPayloadBytes, MaxFutureSkew: cfg.MaxFutureSkew, MaxAge: cfg.MaxEventAge}
	hub := live.NewHub(metrics)

	// Transport. The MQTT ingress handler needs the pipeline and command service,
	// which need the transport, so it is filled in after they exist.
	gateway := &mqttgw.Ingress{Policy: policy, Store: store, Metrics: metrics, Log: log, Limits: limits}
	var transport bus.Bus
	switch cfg.BusBackend {
	case "memory":
		transport = bus.NewMemory()
		log.Warn("in-memory transport: run a single replica; MQTT clients are not served")
	default:
		transport = bus.NewMQTT(bus.MQTTConfig{BrokerURL: cfg.MQTTBrokerURL, Username: cfg.MQTTUsername,
			Password: cfg.MQTTPassword, CACertPath: cfg.MQTTCACertPath, ClientID: cfg.MQTTClientID,
			IngressWorkers: cfg.IngestWorkers * 4}, gateway, metrics, log)
	}

	pipeline := ingest.New(ingest.Config{QueueSize: cfg.QueueSize, Workers: cfg.IngestWorkers, MaxBatch: cfg.MaxBatch,
		WriteRetries: 2}, store, transport, metrics, log)
	pipeline.Start()
	cmds := commands.NewService(commands.Config{AckTimeout: cfg.CommandAckWait, MaxPayloadBytes: cfg.MaxPayloadBytes},
		store, transport, policy, metrics, log)
	gateway.Pipeline, gateway.Commands = pipeline, cmds

	if err := transport.Start(ctx, bus.Handlers{OnLive: hub.OnLive, OnCommand: hub.OnCommand}); err != nil {
		return fmt.Errorf("transport: %w", err)
	}
	defer transport.Close()
	go cmds.Run(ctx)
	if cfg.RetentionDays > 0 {
		go retention(ctx, store, cfg.RetentionDays, log)
	}

	server := api.NewServer(api.Options{
		InternalToken: cfg.InternalToken, Limits: limits, MaxBatch: cfg.MaxBatch,
		RateLimitPerSec: cfg.RateLimitPerSec, RateLimitBurst: cfg.RateLimitBurst,
		QueryMaxLimit: cfg.QueryMaxLimit, QueryMaxBuckets: cfg.QueryMaxBuckets, QueryTimeout: cfg.QueryTimeout,
		LifecycleEvents: cfg.LifecycleEvents, AllowedWSOrigins: cfg.AllowedWSOrigins,
		MQTTJWTSecret: cfg.MQTTJWTSecret, MQTTPublicURL: cfg.MQTTPublicURL, MQTTTokenTTL: cfg.MQTTTokenTTL,
		Ready: func(ctx context.Context) map[string]error {
			checks := map[string]error{"storage": store.Ping(ctx), "transport": nil}
			if !transport.Ready() {
				checks["transport"] = errors.New("not connected")
			}
			return checks
		},
	}, policy, pipeline, store, hub, cmds, metrics, log)

	public := &http.Server{Addr: cfg.HTTPAddr, Handler: server.PublicHandler(), ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout: 120 * time.Second, MaxHeaderBytes: 16 << 10}
	ops := &http.Server{Addr: cfg.OpsAddr, Handler: server.OpsHandler(), ReadHeaderTimeout: 10 * time.Second}
	errs := make(chan error, 2)
	go func() { errs <- serve(public) }()
	go func() { errs <- serve(ops) }()
	log.Info("data plane started", "http_addr", cfg.HTTPAddr, "ops_addr", cfg.OpsAddr,
		"storage", cfg.StorageBackend, "transport", cfg.BusBackend)

	select {
	case <-ctx.Done():
	case err := <-errs:
		if err != nil {
			return err
		}
	}

	// Graceful shutdown: stop accepting, tell streaming clients to reconnect, finish
	// the writes that were already accepted, then disconnect.
	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	server.CloseSessions()
	public.Shutdown(shutdownCtx)
	ops.Shutdown(shutdownCtx)
	pipeline.Stop(shutdownCtx)
	log.Info("stopped")
	return nil
}

func serve(s *http.Server) error {
	if err := s.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("listen %s: %w", s.Addr, err)
	}
	return nil
}

func retention(ctx context.Context, store storage.Store, days int, log *slog.Logger) {
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	for {
		cutoff := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
		dctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
		n, err := store.DeleteEventsBefore(dctx, cutoff)
		cancel()
		if err != nil {
			log.Warn("retention sweep failed", "error", err.Error())
		} else if n > 0 {
			log.Info("retention sweep removed old events", "events", n)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
