package integration

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/chwriter"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/natstest"
	"meteorcloud/dataplane/internal/ruleproc"
)

func metrics(i int) string {
	return fmt.Sprintf(`{"cpu_percent":%d,"timestamp":"%s"}`, i, time.Now().UTC().Add(-time.Duration(i)*time.Second).Format(time.RFC3339))
}

// 1. Publishing: ingestion stores a verified event on the right subject, once.
func TestPublishTelemetryEvent(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	stream := ensureStream(t, js, streamConfig())
	in, im := newIngester(js, nm, reg)

	payload := `{"cpu_percent":35.2,"memory_percent":87.5,"temperature_c":54.1,"timestamp":"` + time.Now().UTC().Format(time.RFC3339) + `"}`
	ingest(t, in, devA, "metrics", payload)
	ingest(t, in, devA, "metrics", payload) // an MQTT redelivery of the same message

	msg, err := stream.GetLastMsgForSubject(context.Background(), "telemetry.v1.>")
	if err != nil {
		t.Fatal(err)
	}
	if msg.Subject != "telemetry.v1."+orgA+"."+devA {
		t.Fatalf("subject %s", msg.Subject)
	}
	if msg.Header.Get(natsjs.HeaderOrganization) != orgA || msg.Header.Get("Nats-Msg-Id") == "" {
		t.Fatalf("headers %v", msg.Header)
	}
	si, _ := stream.Info(context.Background())
	if si.State.Msgs != 1 {
		t.Fatalf("stream holds %d messages, want 1 (duplicate publish must be dropped)", si.State.Msgs)
	}
	if counter(im.Published.WithLabelValues("metrics", "duplicate")) != 1 || counter(nm.Published.WithLabelValues("telemetry.v1", "ok")) != 1 {
		t.Fatal("publish metrics not recorded")
	}
}

// 2, 3, 5. Both consumers receive every event, acknowledge it, and a duplicate
// delivery is stored and evaluated once.
func TestConsumersReceiveAcknowledgeAndDeduplicate(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	cfg := streamConfig()
	cfg.DuplicateWindow = 100 * time.Millisecond
	ensureStream(t, js, cfg)
	in, _ := newIngester(js, nm, reg)
	rules, writer := ensureConsumer(t, js, "rule-processor"), ensureConsumer(t, js, "clickhouse-writer")

	for i := 0; i < 20; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}
	ingest(t, in, devB, "status", `{"status":"online","timestamp":"`+time.Now().UTC().Format(time.RFC3339)+`"}`)
	ingest(t, in, devA, "events", `{"event_id":"evt-1","type":"door.opened"}`)
	// The same event id again after the duplicate window: JetStream stores it a
	// second time, so consumers really do see a duplicate delivery.
	time.Sleep(200 * time.Millisecond)
	ingest(t, in, devA, "events", `{"event_id":"evt-1","type":"door.opened"}`)

	rec := &recorder{}
	stopRules := runProcessor(t, rules, ruleproc.Dedupe(rec, 1000), nm)
	defer stopRules()
	store := &fakeClickHouse{}
	stopWriter, wm := runWriter(t, writer, store, nm, reg, chwriter.Config{BatchSize: 10, FlushInterval: 100 * time.Millisecond})
	defer stopWriter()

	eventually(t, "all events processed and acknowledged", func() bool {
		r, w := info(t, rules), info(t, writer)
		return r.NumPending == 0 && r.NumAckPending == 0 && w.NumPending == 0 && w.NumAckPending == 0 && store.count() == 22
	})
	if rec.count() != 22 {
		t.Fatalf("rule handler evaluated %d events, want 22 (duplicate skipped)", rec.count())
	}
	if got := counter(wm.Rows.WithLabelValues("duplicate")); got != 1 {
		t.Fatalf("writer skipped %v duplicates, want 1", got)
	}
	if counter(nm.Processed.WithLabelValues("clickhouse-writer", "ack")) != 23 || counter(nm.Processed.WithLabelValues("rule-processor", "ack")) != 23 {
		t.Fatal("every delivered message must be acknowledged")
	}
	for _, ev := range rec.events {
		if want := devices[ev.DeviceID]; ev.OrganizationID != want {
			t.Fatalf("event %s: organization %s, want %s", ev.ID, ev.OrganizationID, want)
		}
	}
}

// 4. A consumer stopped and started again resumes where it left off.
func TestConsumerRestartResumes(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	cons := ensureConsumer(t, js, "rule-processor")

	for i := 0; i < 5; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}
	first := &recorder{}
	stop := runProcessor(t, cons, first, nm)
	eventually(t, "first five", func() bool { return first.count() == 5 })
	stop()

	for i := 5; i < 12; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}
	// The server restarts too: stream data and consumer progress are on disk.
	srv.Restart()
	_, js = srv.Connect(t)
	cons = ensureConsumer(t, js, "rule-processor")
	if p := info(t, cons).NumPending; p != 7 {
		t.Fatalf("after restart %d pending, want 7", p)
	}
	second := &recorder{}
	stop = runProcessor(t, cons, second, nm)
	defer stop()
	eventually(t, "remaining seven", func() bool { return second.count() == 7 })
	time.Sleep(200 * time.Millisecond)
	if second.count() != 7 {
		t.Fatalf("restarted consumer processed %d, want exactly 7 (no reprocessing)", second.count())
	}
}

// 6. A handler that fails temporarily gets the event again and finally acks it.
func TestTemporaryConsumerFailureIsRetried(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	cons := ensureConsumer(t, js, "rule-processor")
	ingest(t, in, devA, "metrics", metrics(1))

	rec := &recorder{failN: 2}
	stop := runProcessor(t, cons, rec, nm)
	defer stop()
	eventually(t, "event acknowledged after retries", func() bool {
		return rec.count() == 1 && info(t, cons).NumAckPending == 0
	})
	if n := counter(nm.Processed.WithLabelValues("rule-processor", "nak")); n != 2 {
		t.Fatalf("naks %v, want 2", n)
	}
	if n := counter(nm.Redeliveries.WithLabelValues("rule-processor")); n != 2 {
		t.Fatalf("redeliveries %v, want 2", n)
	}
}

// A handler error marked permanent terminates the message instead of looping.
func TestPermanentFailureIsTerminated(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	cons := ensureConsumer(t, js, "rule-processor")
	ingest(t, in, devA, "metrics", metrics(1))
	calls := 0
	stop := runProcessor(t, cons, ruleproc.HandlerFunc(func(context.Context, *envelope.Event) error {
		calls++
		return ruleproc.Permanent(fmt.Errorf("rule refers to a deleted device group"))
	}), nm)
	defer stop()
	eventually(t, "terminated", func() bool { return counter(nm.Processed.WithLabelValues("rule-processor", "term")) == 1 })
	time.Sleep(200 * time.Millisecond)
	if calls != 1 || info(t, cons).NumAckPending != 0 {
		t.Fatalf("calls %d, ack pending %d", calls, info(t, cons).NumAckPending)
	}
}

// 7. ClickHouse down: nothing is acknowledged or lost; when it returns every
// event is stored exactly once.
func TestClickHouseUnavailable(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	cons := ensureConsumer(t, js, "clickhouse-writer")
	for i := 0; i < 30; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}

	store := &fakeClickHouse{}
	store.down.Store(true)
	stop, wm := runWriter(t, cons, store, nm, reg, chwriter.Config{BatchSize: 10, FlushInterval: 50 * time.Millisecond,
		RetryFor: 300 * time.Millisecond, RetryDelay: 100 * time.Millisecond})
	defer stop()

	eventually(t, "batches handed back to JetStream", func() bool {
		return counter(nm.Processed.WithLabelValues("clickhouse-writer", "nak")) >= 10
	})
	if counter(nm.Processed.WithLabelValues("clickhouse-writer", "ack")) != 0 || store.count() != 0 {
		t.Fatal("nothing may be acknowledged while ClickHouse is down")
	}
	if counter(wm.InsertFailures) == 0 {
		t.Fatal("insert failures not counted")
	}
	store.down.Store(false)
	eventually(t, "all stored after recovery", func() bool {
		i := info(t, cons)
		return store.count() == 30 && i.NumPending == 0 && i.NumAckPending == 0
	})
	if counter(wm.Rows.WithLabelValues("inserted")) != 30 {
		t.Fatalf("inserted %v rows, want 30", counter(wm.Rows.WithLabelValues("inserted")))
	}
}

// 8. Each consumer keeps its own progress: a stopped rule processor does not hold
// back the writer, and the writer's acks do not consume the rule processor's events.
func TestIndependentConsumerProgress(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	rules, writer := ensureConsumer(t, js, "rule-processor"), ensureConsumer(t, js, "clickhouse-writer")
	for i := 0; i < 15; i++ {
		ingest(t, in, devA, "metrics", metrics(i))
	}
	store := &fakeClickHouse{}
	stop, _ := runWriter(t, writer, store, nm, reg, chwriter.Config{BatchSize: 5, FlushInterval: 50 * time.Millisecond})
	eventually(t, "writer done", func() bool { return store.count() == 15 && info(t, writer).NumAckPending == 0 })
	stop()
	if p := info(t, rules).NumPending; p != 15 {
		t.Fatalf("rule processor has %d pending, want all 15", p)
	}
	rec := &recorder{}
	stopRules := runProcessor(t, rules, rec, nm)
	defer stopRules()
	eventually(t, "rule processor catches up", func() bool { return rec.count() == 15 })
}

// 9, 10. Invalid messages and unknown devices are refused before JetStream; a
// device can only produce events under its own organization and device.
func TestInvalidMessagesAndTenantIsolation(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	stream := ensureStream(t, js, streamConfig())
	in, im := newIngester(js, nm, reg)

	ingest(t, in, devA, "metrics", `not json`)
	ingest(t, in, devA, "metrics", `[1]`)
	ingest(t, in, devX, "metrics", metrics(1))              // not enrolled
	ingest(t, in, "not-a-uuid", "metrics", metrics(1))      // bad topic
	ingest(t, in, devA, "commands", `{"command":"reboot"}`) // not an ingest channel
	si, _ := stream.Info(context.Background())
	if si.State.Msgs != 0 {
		t.Fatalf("%d invalid messages reached the stream", si.State.Msgs)
	}
	for reason, want := range map[string]float64{"invalid_payload": 2, "unknown_device": 1, "invalid_topic": 2} {
		if got := counter(im.Rejected.WithLabelValues(reason)); got != want {
			t.Errorf("rejected[%s] = %v, want %v", reason, got, want)
		}
	}

	// Device A claims org B and device B inside its payload.
	ingest(t, in, devA, "metrics", `{"organization_id":"`+orgB+`","device_id":"`+devB+`","cpu_percent":1}`)
	ingest(t, in, devB, "metrics", `{"cpu_percent":2}`)
	ctx := context.Background()
	if _, err := stream.GetLastMsgForSubject(ctx, "telemetry.v1."+orgA+"."+devA); err != nil {
		t.Fatalf("device A's event is not under org A: %v", err)
	}
	a, _ := stream.GetLastMsgForSubject(ctx, "telemetry.v1."+orgB+".>")
	if a == nil || a.Subject != "telemetry.v1."+orgB+"."+devB {
		t.Fatal("org B holds something other than device B's own event")
	}
	si, _ = stream.Info(ctx)
	if si.State.Msgs != 2 {
		t.Fatalf("stream holds %d messages, want 2", si.State.Msgs)
	}

	// A per-tenant consumer (filter on the org token) sees only that tenant.
	cons, err := js.CreateOrUpdateConsumer(ctx, natsjs.DefaultStream, jetstreamConsumer("tenant-a", "telemetry.v1."+orgA+".>"))
	if err != nil {
		t.Fatal(err)
	}
	if p := info(t, cons).NumPending; p != 1 {
		t.Fatalf("tenant A consumer sees %d events, want 1", p)
	}
}

// Ingestion holds a message while NATS is down and stores it once NATS is back.
func TestIngestWaitsForNATS(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, im := newIngester(js, nm, reg)
	srv.Stop()
	done := make(chan bool, 1)
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		done <- in.Handle(ctx, "devices/"+devA+"/metrics", []byte(metrics(1)))
	}()
	time.Sleep(700 * time.Millisecond)
	select {
	case <-done:
		t.Fatal("message acknowledged while NATS was down")
	default:
	}
	srv.Restart()
	if ok := <-done; !ok {
		t.Fatal("message was not stored after NATS came back")
	}
	// The client buffers the publish while reconnecting, or ingestion retries it
	// (publish_retries_total); either way it is stored once.
	_ = im
	_, js2 := srv.Connect(t)
	s, _ := js2.Stream(context.Background(), natsjs.DefaultStream)
	si, _ := s.Info(context.Background())
	if si.State.Msgs != 1 {
		t.Fatalf("stream holds %d messages, want 1", si.State.Msgs)
	}
}
