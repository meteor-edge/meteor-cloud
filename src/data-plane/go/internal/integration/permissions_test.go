package integration

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/chwriter"
	"meteorcloud/dataplane/internal/deviceingest"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/natstest"
)

const confPath = "../../../../../deploy/compose/config/nats/nats.conf"

// 10. The shipped nats.conf: every service works with only its own permissions,
// and nothing more.
func TestNATSConfigPermissions(t *testing.T) {
	for k, v := range map[string]string{"NATS_INGEST_PASSWORD": "pw-ingest", "NATS_RULES_PASSWORD": "pw-rules",
		"NATS_CHWRITER_PASSWORD": "pw-ch", "NATS_ADMIN_PASSWORD": "pw-admin", "NATS_JETSTREAM_MAX_FILE_STORE": "1GB"} {
		t.Setenv(k, v)
	}
	srv := natstest.StartConfig(t, confPath)
	log := quiet
	connect := func(name, user, pw string) (*nats.Conn, jetstream.JetStream) {
		t.Helper()
		nm := natsjs.NewMetrics(prometheus.NewRegistry())
		nc, err := natsjs.Connect(natsjs.ConnConfig{URLs: srv.URL(), Name: name, User: user, Password: pw,
			InboxPrefix: "_INBOX_" + name, ConnectWait: 2 * time.Second, ReconnectWait: 100 * time.Millisecond, MaxReconnects: -1}, nm, log)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(nc.Close)
		js, _ := jetstream.New(nc)
		return nc, js
	}
	ctx := context.Background()

	// Wrong password: refused.
	bad, err := nats.Connect(srv.URL(), nats.UserInfo("ingest", "wrong"))
	if err == nil {
		bad.Close()
		t.Fatal("wrong password accepted")
	}

	ingestNC, ingestJS := connect("device-ingest", "ingest", "pw-ingest")
	if _, err := natsjs.EnsureStream(ctx, ingestJS, streamConfig()); err != nil {
		t.Fatalf("ingest cannot manage its stream: %v", err)
	}
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	in := &deviceingest.Ingester{Dir: devices, Limits: envelope.DefaultLimits(), Metrics: deviceingest.NewMetrics(reg), Log: quiet,
		Publisher: &natsjs.Publisher{JS: ingestJS, Stream: natsjs.DefaultStream, Metrics: nm}}
	for i := 0; i < 3; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}
	// Ingestion can never read events back.
	ingestNC.SubscribeSync("telemetry.v1.>")
	ingestNC.Flush()
	if err := ingestNC.LastError(); err == nil || !strings.Contains(strings.ToLower(err.Error()), "permissions violation") {
		t.Errorf("ingest user may subscribe to telemetry (%v)", err)
	}
	short, cancelShort := context.WithTimeout(ctx, time.Second)
	defer cancelShort()
	if _, err := ingestJS.CreateOrUpdateConsumer(short, natsjs.DefaultStream, jetstreamConsumer("rule-processor", "telemetry.v1.>")); err == nil {
		t.Error("ingest user may create consumers")
	}

	// The writer works through its own consumer.
	_, chJS := connect("clickhouse-writer", "chwriter", "pw-ch")
	chCons, err := natsjs.EnsureConsumer(ctx, chJS, consumerConfig("clickhouse-writer"))
	if err != nil {
		t.Fatalf("writer cannot create its consumer: %v", err)
	}
	store := &fakeClickHouse{}
	stop, _ := runWriter(t, chCons, store, nm, reg, chwriter.Config{BatchSize: 3, FlushInterval: 50 * time.Millisecond})
	eventually(t, "writer stores through its own permissions", func() bool { return store.count() == 3 })
	stop()

	// The rule processor works through its own consumer, and cannot touch the writer's.
	_, rulesJS := connect("rule-processor", "rules", "pw-rules")
	rulesCons, err := natsjs.EnsureConsumer(ctx, rulesJS, consumerConfig("rule-processor"))
	if err != nil {
		t.Fatalf("rule processor cannot create its consumer: %v", err)
	}
	rec := &recorder{}
	stopRules := runProcessor(t, rulesCons, rec, nm)
	eventually(t, "rules consume through their own permissions", func() bool { return rec.count() == 3 })
	stopRules()
	short2, cancelShort2 := context.WithTimeout(ctx, time.Second)
	defer cancelShort2()
	if _, err := natsjs.EnsureConsumer(short2, rulesJS, consumerConfig("clickhouse-writer")); err == nil {
		t.Error("rule processor may bind the writer's consumer")
	}
	pctx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	if _, err := rulesJS.Publish(pctx, "telemetry.v1."+orgA+"."+devA, []byte(`{}`)); err == nil {
		t.Error("rule processor may publish telemetry")
	}
	// A service cannot listen on another service's inbox (fresh connection, so
	// the error cannot be left over from the checks above).
	spy, err := nats.Connect(srv.URL(), nats.UserInfo("rules", "pw-rules"))
	if err != nil {
		t.Fatal(err)
	}
	defer spy.Close()
	denied := make(chan error, 1)
	spy.SetErrorHandler(func(_ *nats.Conn, _ *nats.Subscription, err error) { denied <- err })
	if _, err := spy.SubscribeSync("_INBOX_clickhouse-writer.>"); err != nil {
		t.Fatal(err)
	}
	spy.Flush()
	select {
	case err := <-denied:
		if !strings.Contains(strings.ToLower(err.Error()), "permissions violation") {
			t.Fatalf("unexpected error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Error("rule processor may subscribe to the writer's inbox")
	}
}
