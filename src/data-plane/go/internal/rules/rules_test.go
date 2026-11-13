package rules

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

func TestProcessorConvertsEnvelopeAndEvaluates(t *testing.T) {
	store := state.NewMemory()
	e := engine.New(engine.Config{CommitLinger: time.Millisecond}, store, nil, nil, nil)
	defer e.Close()
	e.Apply(engine.CompileSnapshot(&engine.Snapshot{
		Devices: []engine.Device{{ID: "dev-1", OrganizationID: "org-1"}},
		Rules: []engine.RuleRecord{{ID: "r1", OrganizationID: "org-1", Version: 1, Definition: json.RawMessage(
			`{"schema_version":1,"name":"Hot","scope":{"type":"organization"},"condition":{"type":"comparison","field":"sensors.temp","operator":"gt","value":80}}`)}},
	}, dsl.DefaultLimits()))
	p := &Processor{Engine: e}
	ev := &envelope.Event{ID: "ev-1", SchemaVersion: 1, Kind: envelope.KindTelemetry, Stream: dsl.DefaultStream,
		OrganizationID: "org-1", DeviceID: "dev-1", Time: time.Now(), Data: json.RawMessage(`{"sensors":{"temp":85}}`)}
	if err := p.Handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(store.Outbox()) != 1 {
		t.Fatal("nested payload field evaluated")
	}
	bad := *ev
	bad.ID, bad.Data = "ev-2", json.RawMessage(`[1,2]`)
	if err := p.Handle(context.Background(), &bad); !errors.Is(err, engine.ErrPermanent) {
		t.Fatalf("malformed payload is permanent: %v", err)
	}
	app := *ev
	app.DeviceID = ""
	if err := p.Handle(context.Background(), &app); err != nil {
		t.Fatal(err)
	}
}
