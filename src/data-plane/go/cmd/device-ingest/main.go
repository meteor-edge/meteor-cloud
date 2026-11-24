// Command device-ingest subscribes to device agent topics on the MQTT broker,
// binds each message to a verified device and organization, and stores it as a
// versioned event on NATS JetStream (docs/data-plane/jetstream.md).
//
// Devices never reach NATS: this service is the only bridge, and it
// acknowledges an MQTT message only after JetStream has stored the event.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	"github.com/nats-io/nats.go/jetstream"

	"meteorcloud/dataplane/internal/devicedir"
	"meteorcloud/dataplane/internal/deviceingest"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/service"
)

const opsDefault = ":8093"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(service.Healthcheck("DEVICE_INGEST_OPS_ADDR", opsDefault))
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "device-ingest:", err)
		os.Exit(1)
	}
}

func run() error {
	e := &natsjs.Env{}
	log := observability.NewLogger(os.Stdout, e.String("LOG_FORMAT", "json"), e.String("LOG_LEVEL", "info")).With("service", "device-ingest")
	host, _ := os.Hostname()
	opsAddr := e.String("DEVICE_INGEST_OPS_ADDR", opsDefault)
	connCfg := natsjs.LoadConnConfig(e, "device-ingest", "NATS_INGEST")
	streamCfg := natsjs.LoadStreamConfig(e)
	mqttCfg := deviceingest.MQTTConfig{
		BrokerURL:     e.String("DEVICE_INGEST_MQTT_URL", ""),
		Username:      e.String("MQTT_PLATFORM_USERNAME", "platform"),
		Password:      e.Secret("MQTT_PLATFORM_PASSWORD"),
		CACertPath:    e.String("MQTT_CA_CERT_PATH", ""),
		ClientID:      e.String("DEVICE_INGEST_MQTT_CLIENT_ID", "meteor-device-ingest-"+host),
		SharedGroup:   e.String("DEVICE_INGEST_SHARED_GROUP", "meteor-device-ingest"),
		Workers:       e.Int("DEVICE_INGEST_WORKERS", 16),
		Inflight:      uint16(e.Int("DEVICE_INGEST_INFLIGHT", 512)),
		SessionExpiry: e.Duration("DEVICE_INGEST_SESSION_EXPIRY", 5*time.Minute),
		DrainTimeout:  e.Duration("DEVICE_INGEST_DRAIN_TIMEOUT", 20*time.Second),
	}
	limits := envelope.Limits{
		MaxPayloadBytes: e.Int("DEVICE_INGEST_MAX_PAYLOAD_BYTES", 64<<10),
		MaxFutureSkew:   e.Duration("DEVICE_INGEST_MAX_FUTURE_SKEW", 5*time.Minute),
		MaxAge:          e.Duration("DEVICE_INGEST_MAX_EVENT_AGE", 30*24*time.Hour),
	}
	dirFile := e.String("DEVICE_DIRECTORY_FILE", "")
	controlPlane := e.String("CONTROL_PLANE_URL", "")
	token := e.Secret("DATA_PLANE_INTERNAL_TOKEN")
	dirInterval := e.Duration("DEVICE_DIRECTORY_INTERVAL", 30*time.Second)
	dirMaxStale := e.Duration("DEVICE_DIRECTORY_MAX_STALE", 15*time.Minute)
	if e.Err != nil {
		return e.Err
	}
	if mqttCfg.BrokerURL == "" {
		return errors.New("DEVICE_INGEST_MQTT_URL is required")
	}
	if dirFile == "" && (controlPlane == "" || token == "") {
		return errors.New("CONTROL_PLANE_URL and DATA_PLANE_INTERNAL_TOKEN are required (or DEVICE_DIRECTORY_FILE for local development)")
	}

	ctx, stop := service.SignalContext()
	defer stop()
	reg := service.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	im := deviceingest.NewMetrics(reg)

	dir := devicedir.New(dirMaxStale)
	var src devicedir.Source = &devicedir.HTTPSource{URL: controlPlane, Token: token, Client: &http.Client{Timeout: 30 * time.Second}}
	if dirFile != "" {
		src = &devicedir.FileSource{Path: dirFile}
		log.Warn("device directory comes from a file; use the control plane in production", "path", dirFile)
	}
	syncer := &devicedir.Syncer{Dir: dir, Source: src, Interval: dirInterval, Log: log, Syncs: im.DirSyncs, Devices: im.DirDevices}
	go syncer.Run(ctx)

	nc, err := natsjs.Connect(connCfg, nm, log)
	if err != nil {
		return err
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}

	var current atomic.Pointer[deviceingest.Subscriber] // set once the stream exists
	ops := service.Ops(opsAddr, reg, func(context.Context) map[string]error {
		checks := map[string]error{"device_directory": dir.Ready(), "nats": nil, "mqtt": nil}
		if !nc.IsConnected() {
			checks["nats"] = errors.New("not connected")
		}
		if sub := current.Load(); sub == nil || !sub.Ready() {
			checks["mqtt"] = errors.New("not subscribed")
		}
		return checks
	})
	errs := make(chan error, 1)
	go service.Serve(ops, errs)

	// Only start taking device messages once the stream exists.
	if err := natsjs.Retry(ctx, log, "stream "+streamCfg.Name, func(c context.Context) error {
		_, err := natsjs.EnsureStream(c, js, streamCfg)
		return err
	}); err != nil {
		return err
	}
	log.Info("stream ready", "stream", streamCfg.Name, "subjects", streamCfg.Subjects, "replicas", streamCfg.Replicas)

	ingester := &deviceingest.Ingester{Dir: dir, Limits: limits, Metrics: im, Log: log,
		Publisher: &natsjs.Publisher{JS: js, Stream: streamCfg.Name, Metrics: nm}}
	sub := deviceingest.NewSubscriber(mqttCfg, ingester, im, log)
	current.Store(sub)
	done := make(chan error, 1)
	go func() { done <- sub.Run(ctx) }()
	log.Info("device ingestion started", "ops_addr", opsAddr, "mqtt_topics", mqttCfg.Topics())

	select {
	case err = <-errs:
		stop()
		<-done
	case err = <-done:
	}
	sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ops.Shutdown(sctx)
	if derr := nc.Drain(); derr != nil {
		log.Debug("NATS drain", "error", derr.Error())
	}
	log.Info("stopped")
	return err
}
