package envelope

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/identity"
)

func ident() *identity.Identity {
	return &identity.Identity{
		OrganizationID: "org-1", ApplicationID: "app-1", Application: "collector", DeviceID: "dev-1",
		Environment: "production", Permissions: identity.Permissions{Publish: []string{"energy.*"}},
	}
}

func TestNormalizeTakesIdentityFromCredential(t *testing.T) {
	now := time.Date(2026, 11, 11, 12, 0, 0, 0, time.UTC)
	// A body that tries to smuggle identity fields: they are simply not part of Message.
	raw := `{"stream":"energy.power","organization_id":"evil","device_id":"evil","data":{"power":1.5}}`
	var msg Message
	if err := json.Unmarshal([]byte(raw), &msg); err != nil {
		t.Fatal(err)
	}
	ev, rej := Normalize(&msg, ident(), now, DefaultLimits())
	if rej != nil {
		t.Fatal(rej)
	}
	if ev.OrganizationID != "org-1" || ev.DeviceID != "dev-1" || ev.ApplicationID != "app-1" {
		t.Fatalf("identity not from credential: %+v", ev)
	}
	if ev.Kind != KindTelemetry || ev.Type != "energy.power" || ev.ID == "" || !ev.Time.Equal(now) || ev.SchemaVersion != 1 {
		t.Fatalf("defaults wrong: %+v", ev)
	}
}

func TestNormalizeRejections(t *testing.T) {
	now := time.Now()
	future := now.Add(time.Hour)
	old := now.Add(-40 * 24 * time.Hour)
	cases := []struct {
		name string
		msg  Message
		code string
	}{
		{"bad stream", Message{Stream: "Energy Power", Data: json.RawMessage(`{}`)}, CodeInvalidStream},
		{"wildcard stream", Message{Stream: "energy.*", Data: json.RawMessage(`{}`)}, CodeInvalidStream},
		{"not permitted", Message{Stream: "water.flow", Data: json.RawMessage(`{}`)}, CodeForbiddenStream},
		{"bad kind", Message{Stream: "energy.power", Kind: "lifecycle", Data: json.RawMessage(`{}`)}, CodeInvalidKind},
		{"bad id", Message{Stream: "energy.power", ID: "has space", Data: json.RawMessage(`{}`)}, CodeInvalidID},
		{"array payload", Message{Stream: "energy.power", Data: json.RawMessage(`[1]`)}, CodeInvalidPayload},
		{"broken json", Message{Stream: "energy.power", Data: json.RawMessage(`{"a":`)}, CodeInvalidPayload},
		{"missing payload", Message{Stream: "energy.power"}, CodeInvalidPayload},
		{"too large", Message{Stream: "energy.power", Data: json.RawMessage(`{"a":"` + strings.Repeat("x", 70000) + `"}`)}, CodePayloadTooLarge},
		{"future", Message{Stream: "energy.power", Time: &future, Data: json.RawMessage(`{}`)}, CodeInvalidTimestamp},
		{"too old", Message{Stream: "energy.power", Time: &old, Data: json.RawMessage(`{}`)}, CodeInvalidTimestamp},
	}
	for _, c := range cases {
		_, rej := Normalize(&c.msg, ident(), now, DefaultLimits())
		if rej == nil || rej.Code != c.code {
			t.Errorf("%s: want %s, got %v", c.name, c.code, rej)
		}
	}
}

func TestReservedStreamsCannotBePublished(t *testing.T) {
	id := ident()
	id.Permissions.Publish = []string{"meteor.*", "*.*"}
	_, rej := Normalize(&Message{Stream: "meteor.lifecycle", Data: json.RawMessage(`{}`)}, id, time.Now(), DefaultLimits())
	if rej == nil || rej.Code != CodeForbiddenStream {
		t.Fatalf("reserved stream accepted: %v", rej)
	}
}

func TestOldButAcceptableTimestampKept(t *testing.T) {
	now := time.Now()
	ts := now.Add(-48 * time.Hour) // an edge device that was offline for two days
	ev, rej := Normalize(&Message{Stream: "energy.power", Time: &ts, Data: json.RawMessage(`{"p":1}`)}, ident(), now, DefaultLimits())
	if rej != nil {
		t.Fatal(rej)
	}
	if !ev.Time.Equal(ts) || !ev.ReceivedAt.After(ev.Time) {
		t.Fatal("source time must be preserved and received_at set by the server")
	}
}
