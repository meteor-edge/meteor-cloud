// Command rule-processor consumes device events from its own durable JetStream
// consumer and evaluates real-time rules on them (docs/data-plane/jetstream.md).
//
// The rule engine plugs in as a ruleproc.Handler (see newHandler). Until then the
// default handler counts and logs events, which demonstrates the delivery path:
// explicit acks, retries with backoff and independent scaling of workers.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/ruleproc"
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

// newHandler is where the rule engine is wired in.
func newHandler(reg prometheus.Registerer, log *slog.Logger) ruleproc.Handler {
	events := prometheus.NewCounterVec(prometheus.CounterOpts{Namespace: "meteorcloud_rule_processor", Name: "events_total",
		Help: "Events handed to the rule handler, by kind."}, []string{"kind"})
	reg.MustRegister(events)
	return ruleproc.Dedupe(&ruleproc.LogHandler{Log: log, Events: events}, 100000)
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
	if e.Err != nil {
		return e.Err
	}
	return service.RunConsumer(log, opsAddr, connCfg, consCfg, func(ctx context.Context, cons jetstream.Consumer, reg prometheus.Registerer, nm *natsjs.Metrics) {
		p := &ruleproc.Processor{Cfg: cfg, Cons: cons, Handler: newHandler(reg, log), Metrics: nm, Log: log}
		p.Run(ctx)
	})
}
