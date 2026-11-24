// Command clickhouse-writer consumes device events from its own durable JetStream
// consumer and writes them to ClickHouse in batches, acknowledging each message
// only after its batch is stored (docs/data-plane/jetstream.md).
package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/chwriter"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/service"
	"meteorcloud/dataplane/internal/storage"
)

const opsDefault = ":8095"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(service.Healthcheck("CLICKHOUSE_WRITER_OPS_ADDR", opsDefault))
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "clickhouse-writer:", err)
		os.Exit(1)
	}
}

func run() error {
	e := &natsjs.Env{}
	log := observability.NewLogger(os.Stdout, e.String("LOG_FORMAT", "json"), e.String("LOG_LEVEL", "info")).With("service", "clickhouse-writer")
	opsAddr := e.String("CLICKHOUSE_WRITER_OPS_ADDR", opsDefault)
	connCfg := natsjs.LoadConnConfig(e, "clickhouse-writer", "NATS_CHWRITER")
	batch := e.Int("CLICKHOUSE_WRITER_BATCH_SIZE", 1000)
	consCfg := natsjs.LoadConsumerConfig(e, "CLICKHOUSE_WRITER_CONSUMER", natsjs.ConsumerConfig{
		Durable:        "clickhouse-writer",
		FilterSubjects: []string{"telemetry.v1.>", "device.status.v1.>", "device.events.v1.>"},
		AckWait:        time.Minute,
		MaxDeliver:     -1, // storage never gives up on a message; stream limits bound an outage
		MaxAckPending:  max(batch*4, 4000),
		FetchBatch:     min(batch, 500),
		FetchMaxWait:   time.Second,
	})
	cfg := chwriter.Config{
		Name:          consCfg.Durable,
		BatchSize:     batch,
		FlushInterval: e.Duration("CLICKHOUSE_WRITER_FLUSH_INTERVAL", time.Second),
		InsertTimeout: e.Duration("CLICKHOUSE_WRITER_INSERT_TIMEOUT", 15*time.Second),
		RetryFor:      e.Duration("CLICKHOUSE_WRITER_RETRY_FOR", 20*time.Second),
		RetryDelay:    e.Duration("CLICKHOUSE_WRITER_RETRY_DELAY", 10*time.Second),
		FetchMax:      consCfg.FetchBatch,
	}
	chOpts := storage.ClickHouseOptions{
		URL:           e.String("CLICKHOUSE_URL", "http://clickhouse:8123"),
		Database:      e.String("CLICKHOUSE_DB", "meteorcloud"),
		User:          e.String("CLICKHOUSE_USER", "default"),
		Password:      e.Secret("CLICKHOUSE_PASSWORD"),
		QueryTimeout:  cfg.InsertTimeout,
		RetentionDays: e.Int("DATAPLANE_RETENTION_DAYS", 0),
	}
	if e.Err != nil {
		return e.Err
	}
	if cfg.FlushInterval+cfg.RetryFor >= consCfg.AckWait {
		return errors.New("CLICKHOUSE_WRITER_FLUSH_INTERVAL + CLICKHOUSE_WRITER_RETRY_FOR must be below CLICKHOUSE_WRITER_CONSUMER_ACK_WAIT")
	}

	// ClickHouse may start after this service: keep trying, consume nothing until it answers.
	ctx, stop := service.SignalContext()
	var ch *storage.ClickHouse
	err := natsjs.Retry(ctx, log, "clickhouse", func(c context.Context) error {
		var err error
		ch, err = storage.OpenClickHouse(c, chOpts)
		return err
	})
	stop()
	if err != nil {
		return err
	}
	defer ch.Close()

	return service.RunConsumer(log, opsAddr, connCfg, consCfg, func(ctx context.Context, cons jetstream.Consumer, reg prometheus.Registerer, nm *natsjs.Metrics) {
		w := &chwriter.Writer{Cfg: cfg, Cons: cons, Store: ch, NATS: nm, Metrics: chwriter.NewMetrics(reg), Log: log}
		w.Run(ctx)
	})
}
