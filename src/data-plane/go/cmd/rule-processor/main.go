// Command rule-processor consumes device events from its own durable JetStream
// consumer and evaluates real-time rules on them (docs/data-plane/jetstream.md,
// contracts/rules-v1.md).
//
// With RULES_DATABASE_URL set, it runs the rule engine: rules come from the control
// plane's snapshot, evaluation state, timers and the action outbox live in the
// PostgreSQL schema "rules", and a dispatcher delivers actions. Without it, the
// default handler only counts and logs events (local development of the event path).
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/ruleproc"
	"meteorcloud/dataplane/internal/rules"
	"meteorcloud/dataplane/internal/rules/dispatch"
	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
	"meteorcloud/dataplane/internal/service"
)

const opsDefault = ":8094"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(service.Healthcheck("RULES_OPS_ADDR", opsDefault))
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "rule-processor:", err)
		os.Exit(1)
	}
}

// engineConfig is everything the rule engine needs besides the event stream.
type engineConfig struct {
	databaseURL   string
	maxConns      int32
	controlPlane  string
	token         string
	snapshotEvery time.Duration
	apiAddr       string
	dispatch      bool
	allowPrivate  bool
	dispatchCfg   dispatch.Config
	engine        engine.Config
}

func loadEngineConfig(e *natsjs.Env) engineConfig {
	return engineConfig{
		databaseURL:   e.String("RULES_DATABASE_URL", ""),
		maxConns:      int32(e.Int("RULES_DATABASE_MAX_CONNS", 20)),
		controlPlane:  e.String("CONTROL_PLANE_URL", ""),
		token:         e.Secret("DATA_PLANE_INTERNAL_TOKEN"),
		snapshotEvery: e.Duration("RULES_SNAPSHOT_INTERVAL", 15*time.Second),
		apiAddr:       e.String("RULES_API_ADDR", ":8095"),
		dispatch:      e.String("RULES_DISPATCH", "true") != "false",
		allowPrivate:  e.String("RULES_WEBHOOK_ALLOW_PRIVATE", "false") == "true",
		dispatchCfg: dispatch.Config{
			Workers:     e.Int("RULES_DISPATCH_WORKERS", 8),
			MaxAttempts: e.Int("RULES_DISPATCH_MAX_ATTEMPTS", 10),
			Backoff: e.Durations("RULES_DISPATCH_BACKOFF",
				[]time.Duration{5 * time.Second, 30 * time.Second, 2 * time.Minute, 10 * time.Minute, 30 * time.Minute}),
		},
		engine: engine.Config{
			CommitBatch:  e.Int("RULES_COMMIT_BATCH", 64),
			CommitLinger: e.Duration("RULES_COMMIT_LINGER", 2*time.Millisecond),
			TimerEvery:   e.Duration("RULES_TIMER_INTERVAL", time.Second),
			Limits:       dsl.DefaultLimits(),
		},
	}
}

// logHandler is the handler without the rule engine.
func logHandler(reg prometheus.Registerer, log *slog.Logger) ruleproc.Handler {
	events := prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: "meteorcloud_rule_processor", Name: "events_total",
		Help: "Events handed to the rule handler, by kind."}, []string{"kind"})
	reg.MustRegister(events)
	return ruleproc.Dedupe(&ruleproc.LogHandler{Log: log, Events: events}, 100000)
}

// runEngine starts the engine's background work (snapshot sync, timers, dispatcher,
// internal API) and returns the event handler and a function that stops it all.
func runEngine(ctx context.Context, cfg engineConfig, store *state.Postgres, reg prometheus.Registerer, log *slog.Logger) (ruleproc.Handler, func()) {
	eng := engine.New(cfg.engine, store, nil, engine.NewMetrics(reg), log.With("component", "engine"))
	ctx, cancel := context.WithCancel(ctx)
	var wg sync.WaitGroup
	start := func(fn func(context.Context)) {
		wg.Add(1)
		go func() { defer wg.Done(); fn(ctx) }()
	}
	cp := &dispatch.ControlPlane{URL: cfg.controlPlane, Token: cfg.token}
	syncer := &rules.Syncer{Engine: eng, Interval: cfg.snapshotEvery, Limits: cfg.engine.Limits, Log: log,
		Source: &rules.HTTPSnapshotSource{URL: cfg.controlPlane, Token: cfg.token}}
	start(syncer.Run)
	start(eng.RunTimers)
	if cfg.dispatch {
		webhooks := &dispatch.WebhookSender{Resolver: &dispatch.ControlPlaneResolver{CP: cp},
			Client: dispatch.NewWebhookClient(cfg.allowPrivate)}
		d := dispatch.New(cfg.dispatchCfg, store, map[string]dispatch.Sender{
			dsl.ActionAlert:         &dispatch.AlertSender{CP: cp},
			dsl.ActionWebhook:       webhooks,
			dsl.ActionNotification:  webhooks,
			dsl.ActionCommand:       &dispatch.CommandSender{CP: cp},
			dsl.ActionPlatformEvent: &dispatch.PlatformEventSender{CP: cp},
		}, nil, dispatch.NewMetrics(reg), log.With("component", "dispatcher"))
		start(d.Run)
	}
	api := &http.Server{Addr: cfg.apiAddr, ReadHeaderTimeout: 5 * time.Second,
		Handler: (&rules.API{Engine: eng, Token: cfg.token, Limits: cfg.engine.Limits}).Handler()}
	go func() {
		if err := api.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("rules API stopped", "addr", cfg.apiAddr, "error", err.Error())
		}
	}()
	proc := &rules.Processor{Engine: eng}
	handler := ruleproc.HandlerFunc(func(ctx context.Context, ev *envelope.Event) error {
		err := proc.Handle(ctx, ev)
		if errors.Is(err, engine.ErrPermanent) {
			return ruleproc.Permanent(err)
		}
		return err // ErrNotReady and storage errors: redelivered with backoff
	})
	return handler, func() {
		cancel()
		sctx, scancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer scancel()
		api.Shutdown(sctx)
		wg.Wait()
		eng.Close()
	}
}

func run() error {
	e := &natsjs.Env{}
	log := observability.NewLogger(os.Stdout, e.String("LOG_FORMAT", "json"), e.String("LOG_LEVEL", "info")).With("service", "rule-processor")
	opsAddr := e.String("RULES_OPS_ADDR", opsDefault)
	connCfg := natsjs.LoadConnConfig(e, "rule-processor", "NATS_RULES")
	consCfg := natsjs.LoadConsumerConfig(e, "RULES_CONSUMER", natsjs.ConsumerConfig{
		Durable:        "rule-processor",
		FilterSubjects: []string{"telemetry.v1.>", "device.status.v1.>", "device.events.v1.>"},
		AckWait:        30 * time.Second,
		MaxDeliver:     10,
		MaxAckPending:  2000,
		FetchBatch:     100,
		FetchMaxWait:   time.Second,
	})
	cfg := ruleproc.Config{
		Name:           consCfg.Durable,
		Workers:        e.Int("RULES_WORKERS", 8),
		HandlerTimeout: e.Duration("RULES_HANDLER_TIMEOUT", 10*time.Second),
		RetryBackoff:   e.Durations("RULES_RETRY_BACKOFF", []time.Duration{time.Second, 5 * time.Second, 30 * time.Second, 2 * time.Minute}),
		FetchBatch:     consCfg.FetchBatch,
		FetchMaxWait:   consCfg.FetchMaxWait,
	}
	ecfg := loadEngineConfig(e)
	if e.Err != nil {
		return e.Err
	}
	var store *state.Postgres
	if ecfg.databaseURL != "" {
		if ecfg.controlPlane == "" || ecfg.token == "" {
			return errors.New("CONTROL_PLANE_URL and DATA_PLANE_INTERNAL_TOKEN are required with RULES_DATABASE_URL")
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		var err error
		store, err = state.OpenPostgres(ctx, ecfg.databaseURL, ecfg.maxConns)
		cancel()
		if err != nil {
			return fmt.Errorf("open rules database: %w", err)
		}
		defer store.Close()
	} else {
		log.Warn("RULES_DATABASE_URL is not set: rules are not evaluated, events are only counted")
	}
	return service.RunConsumer(log, opsAddr, connCfg, consCfg, func(ctx context.Context, cons jetstream.Consumer, reg prometheus.Registerer, nm *natsjs.Metrics) {
		var handler ruleproc.Handler
		if store != nil {
			h, stop := runEngine(ctx, ecfg, store, reg, log)
			defer stop()
			handler = h
		} else {
			handler = logHandler(reg, log)
		}
		p := &ruleproc.Processor{Cfg: cfg, Cons: cons, Handler: handler, Metrics: nm, Log: log}
		p.Run(ctx)
	})
}
