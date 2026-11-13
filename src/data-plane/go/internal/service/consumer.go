package service

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/natsjs"
)

// RunConsumer connects, binds the durable consumer, serves ops and runs fn until
// SIGINT/SIGTERM. fn must return once ctx ends, after finishing its batch in
// progress; the connection is drained afterwards.
func RunConsumer(log *slog.Logger, opsAddr string, connCfg natsjs.ConnConfig, consCfg natsjs.ConsumerConfig,
	fn func(context.Context, jetstream.Consumer, prometheus.Registerer, *natsjs.Metrics)) error {
	ctx, stop := SignalContext()
	defer stop()
	reg := NewRegistry()
	nm := natsjs.NewMetrics(reg)
	nc, err := natsjs.Connect(connCfg, nm, log)
	if err != nil {
		return err
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	ops := Ops(opsAddr, reg, func(context.Context) map[string]error {
		if !nc.IsConnected() {
			return map[string]error{"nats": errors.New("not connected")}
		}
		return map[string]error{"nats": nil}
	})
	errs := make(chan error, 1)
	go Serve(ops, errs)
	var cons jetstream.Consumer
	if err := natsjs.Retry(ctx, log, "consumer "+consCfg.Durable, func(c context.Context) error {
		cons, err = natsjs.EnsureConsumer(c, js, consCfg)
		return err
	}); err != nil {
		return err
	}
	go natsjs.WatchConsumer(ctx, cons, consCfg.Durable, 10*time.Second, nm)
	log.Info("consuming", "stream", consCfg.Stream, "durable", consCfg.Durable, "filter_subjects", consCfg.FilterSubjects, "ops_addr", opsAddr)
	done := make(chan struct{})
	go func() { fn(ctx, cons, reg, nm); close(done) }()
	select {
	case err = <-errs:
		stop()
	case <-ctx.Done():
	}
	<-done // the batch in progress is finished and acknowledged
	sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ops.Shutdown(sctx)
	nc.Drain()
	log.Info("stopped")
	return err
}
