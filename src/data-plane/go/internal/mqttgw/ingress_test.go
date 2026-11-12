package mqttgw

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/ingest"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

// directSubmit stores straight into the store, failing the first `failures` calls.
type directSubmit struct {
	mu       sync.Mutex
	store    storage.Store
	failures int
	err      error
	calls    int
}

func (d *directSubmit) Submit(ctx context.Context, events []*envelope.Event) ([]string, error) {
	d.mu.Lock()
	d.calls++
	fail := d.calls <= d.failures
	d.mu.Unlock()
	if fail {
		return nil, d.err
	}
	inserted, err := d.store.InsertEvents(ctx, events)
	if err != nil {
		return nil, err
	}
	statuses := make([]string, len(inserted))
	for i, fresh := range inserted {
		statuses[i] = "duplicate"
		if fresh {
			statuses[i] = "stored"
		}
	}
	return statuses, nil
}

type fixture struct {
	gw     *Ingress
	store  *storage.Memory
	submit *directSubmit
	cmds   *commands.Service
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	policy := identity.NewStore(0)
	policy.Apply(&identity.Snapshot{Version: "1", Applications: []identity.SnapshotApplication{
		{ID: "app-meter", OrganizationID: "org", Slug: "meter", Permissions: identity.Permissions{
			Publish: []string{"energy.**"}, ReceiveCommands: true}},
		{ID: "app-reader", OrganizationID: "org", Slug: "reader"},
	}})
	store := storage.NewMemory()
	metrics := observability.NewMetrics()
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	b := bus.NewMemory()
	b.Start(context.Background(), bus.Handlers{})
	submit := &directSubmit{store: store, err: errors.New("database is down")}
	cmds := commands.NewService(commands.Config{AckTimeout: time.Second, MaxBackoff: time.Second}, store, b, policy,
		metrics, log)
	return &fixture{store: store, submit: submit, cmds: cmds, gw: &Ingress{Policy: policy, Pipeline: submit,
		Commands: cmds, Store: store, Metrics: metrics, Log: log, Limits: envelope.DefaultLimits(),
		MaxWait: 2 * time.Second}}
}

func topic(app, device, kind, stream string) bus.Topic {
	return bus.Topic{OrgID: "org", App: app, Device: device, Kind: kind, Stream: stream, HasDevice: device != ""}
}

func (f *fixture) events(t *testing.T) []*envelope.Event {
	t.Helper()
	events, _, err := f.store.QueryEvents(context.Background(), storage.Query{OrganizationID: "org",
		Stream: "energy.metrics", Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	return events
}

func TestMessageIsStoredWithIdentityFromTheTopic(t *testing.T) {
	f := newFixture(t)
	// The payload claims another organization and device; only the topic counts.
	payload := []byte(`{"id":"m-1","seq":7,"data":{"power":1200},"organization_id":"evil","device_id":"evil"}`)
	if !f.gw.Message(context.Background(), topic("meter", "dev-1", "telemetry", "energy.metrics"), payload) {
		t.Fatal("a stored message must be acknowledged")
	}
	events := f.events(t)
	if len(events) != 1 {
		t.Fatalf("stored %d events, want 1", len(events))
	}
	ev := events[0]
	if ev.ID != "m-1" || ev.OrganizationID != "org" || ev.Application != "meter" || ev.ApplicationID != "app-meter" ||
		ev.DeviceID != "dev-1" || ev.Kind != "telemetry" || ev.Seq == nil || *ev.Seq != 7 {
		t.Fatalf("unexpected envelope: %+v", ev)
	}
	// Redelivery by the broker stores nothing twice.
	f.gw.Message(context.Background(), topic("meter", "dev-1", "telemetry", "energy.metrics"), payload)
	if len(f.events(t)) != 1 || len(f.store.DeadLetters()) != 0 {
		t.Fatal("a redelivered message must be deduplicated, not dead-lettered")
	}
}

func TestUnacceptableMessagesAreDeadLetteredNotDropped(t *testing.T) {
	cases := []struct {
		name    string
		topic   bus.Topic
		payload string
		reason  string
	}{
		{"not json", topic("meter", "d", "telemetry", "energy.metrics"), `power=1`, envelope.CodeInvalidPayload},
		{"data is not an object", topic("meter", "d", "telemetry", "energy.metrics"), `{"data":[1]}`, envelope.CodeInvalidPayload},
		{"no publish permission", topic("meter", "d", "telemetry", "billing.invoices"), `{"data":{}}`, envelope.CodeForbiddenStream},
		{"application without grants", topic("reader", "d", "telemetry", "energy.metrics"), `{"data":{}}`, envelope.CodeForbiddenStream},
		{"unknown application", topic("ghost", "d", "telemetry", "energy.metrics"), `{"data":{}}`, "unknown_application"},
		{"bad id", topic("meter", "d", "telemetry", "energy.metrics"), `{"id":"has space","data":{}}`, envelope.CodeInvalidID},
		{"future timestamp", topic("meter", "d", "telemetry", "energy.metrics"), `{"time":"2999-01-01T00:00:00Z","data":{}}`, envelope.CodeInvalidTimestamp},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFixture(t)
			if !f.gw.Message(context.Background(), tc.topic, []byte(tc.payload)) {
				t.Fatal("a dead-lettered message is handled and must be acknowledged")
			}
			dead := f.store.DeadLetters()
			if len(dead) != 1 || dead[0].Reason != tc.reason || dead[0].OrganizationID != "org" ||
				dead[0].Source != "mqtt" || string(dead[0].Payload) != tc.payload {
				t.Fatalf("dead letters: %+v", dead)
			}
			if len(f.events(t)) != 0 {
				t.Fatal("nothing may be stored")
			}
		})
	}
}

func TestOversizedPayloadIsTruncatedInTheDeadLetter(t *testing.T) {
	f := newFixture(t)
	f.gw.Limits.MaxPayloadBytes = 64
	big := make([]byte, 5000)
	for i := range big {
		big[i] = 'x'
	}
	f.gw.Message(context.Background(), topic("meter", "d", "telemetry", "energy.metrics"), big)
	dead := f.store.DeadLetters()
	if len(dead) != 1 || dead[0].Reason != envelope.CodePayloadTooLarge || len(dead[0].Payload) != 64+1024 {
		t.Fatalf("dead letters: reason=%s size=%d", dead[0].Reason, len(dead[0].Payload))
	}
}

func TestStorageOutageIsRetriedThenStored(t *testing.T) {
	f := newFixture(t)
	f.submit.failures = 2
	if !f.gw.Message(context.Background(), topic("meter", "d", "telemetry", "energy.metrics"), []byte(`{"data":{"v":1}}`)) {
		t.Fatal("message must be handled once storage is back")
	}
	if len(f.events(t)) != 1 || len(f.store.DeadLetters()) != 0 || f.submit.calls != 3 {
		t.Fatalf("events=%d dead=%d calls=%d", len(f.events(t)), len(f.store.DeadLetters()), f.submit.calls)
	}
}

func TestStorageOutageBeyondTheLimitIsDeadLettered(t *testing.T) {
	f := newFixture(t)
	f.submit.failures = 1 << 30
	f.gw.MaxWait = 250 * time.Millisecond
	if !f.gw.Message(context.Background(), topic("meter", "d", "telemetry", "energy.metrics"), []byte(`{"data":{"v":1}}`)) {
		t.Fatal("a dead-lettered message must be acknowledged")
	}
	dead := f.store.DeadLetters()
	if len(dead) != 1 || dead[0].Reason != "storage_unavailable" {
		t.Fatalf("dead letters: %+v", dead)
	}
}

func TestShutdownLeavesTheMessageUnacknowledged(t *testing.T) {
	f := newFixture(t)
	f.submit.failures = 1 << 30
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()
	if f.gw.Message(ctx, topic("meter", "d", "telemetry", "energy.metrics"), []byte(`{"data":{"v":1}}`)) {
		t.Fatal("a message that was not stored must stay unacknowledged so the broker redelivers it")
	}
	f.submit.err = ingest.ErrStopped
	if f.gw.Message(context.Background(), topic("meter", "d", "telemetry", "energy.metrics"), []byte(`{"data":{"v":1}}`)) {
		t.Fatal("a stopped pipeline must leave the message unacknowledged")
	}
	if len(f.store.DeadLetters()) != 0 {
		t.Fatal("shutdown must not dead-letter")
	}
}

func TestCommandAckOverMQTT(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	cmd, err := f.cmds.Create(ctx, commands.CreateRequest{OrganizationID: "org", Application: "meter", DeviceID: "dev-1",
		Name: "reboot", IssuedBy: "user:1"})
	if err != nil {
		t.Fatal(err)
	}
	ackTopic := topic("meter", "dev-1", "ack", "")
	f.gw.CommandAck(ctx, ackTopic, []byte(`{"command_id":"`+cmd.ID+`","status":"succeeded","result":{"ok":true}}`))
	got, err := f.store.GetCommand(ctx, "org", cmd.ID)
	if err != nil || got.State != storage.StateSucceeded || string(got.Result) != `{"ok":true}` {
		t.Fatalf("command after ack: %+v err=%v", got, err)
	}

	// Another application, a malformed ack and an unknown command are all recorded.
	other, _ := f.cmds.Create(ctx, commands.CreateRequest{OrganizationID: "org", Application: "meter", DeviceID: "dev-1",
		Name: "reboot", IssuedBy: "user:1"})
	f.gw.CommandAck(ctx, topic("reader", "dev-1", "ack", ""), []byte(`{"command_id":"`+other.ID+`","status":"succeeded"}`))
	f.gw.CommandAck(ctx, topic("meter", "dev-2", "ack", ""), []byte(`{"command_id":"`+other.ID+`","status":"succeeded"}`))
	f.gw.CommandAck(ctx, ackTopic, []byte(`{"status":"succeeded"}`))
	f.gw.CommandAck(ctx, ackTopic, []byte(`{"command_id":"nope","status":"succeeded"}`))
	f.gw.CommandAck(ctx, topic("ghost", "dev-1", "ack", ""), []byte(`{"command_id":"`+other.ID+`","status":"succeeded"}`))
	reasons := map[string]int{}
	for _, d := range f.store.DeadLetters() {
		reasons[d.Reason]++
	}
	if reasons["rejected_ack"] != 3 || reasons["invalid_ack"] != 1 || reasons["unknown_application"] != 1 {
		t.Fatalf("dead letter reasons: %v", reasons)
	}
	still, _ := f.store.GetCommand(ctx, "org", other.ID)
	if still.State == storage.StateSucceeded {
		t.Fatal("a command must only be acknowledged by its own target")
	}
}
