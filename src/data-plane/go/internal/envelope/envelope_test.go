package envelope

import (
	"bytes"
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

func TestUnstorableEscapes(t *testing.T) {
	cases := map[string]bool{
		`{"v":"a\u0000b"}`:         true,
		`{"v":"a\\u0000b"}`:        false, // an escaped backslash, not a NUL
		`{"v":"\ud800"}`:           true,  // high surrogate alone
		`{"v":"\udc00"}`:           true,  // low surrogate alone
		`{"v":"😀"}`:                false, // a pair
		`{"v":"\ud83dx"}`:          true,
		`{"v":"\ud83d\\ude00"}`:    true,
		`{"v":"é \\ud800"}`:        false,
		`{"v":"plain \"quoted\""}`: false,
	}
	for in, want := range cases {
		if got := hasUnstorableEscape([]byte(in)); got != want {
			t.Errorf("%s: got %v, want %v", in, got, want)
		}
	}
}

func TestNormalizeAcceptsEnergyConsumption(t *testing.T) {
	now := time.Now().UTC()
	msg := Message{
		Stream: "energy.metrics",
		Kind:   KindTelemetry,
		Data: json.RawMessage(`{
			"power": 1234.5,
			"voltage": 230,
			"energy_kwh": 12.25,
			"phases": {"l1": 410.1, "l2": 405.0, "l3": 419.4},
			"meter_id": "m-42"
		}`),
	}
	ev, rej := Normalize(&msg, ident(), now, DefaultLimits())
	if rej != nil {
		t.Fatal(rej)
	}
	var got map[string]any
	if err := json.Unmarshal(ev.Data, &got); err != nil {
		t.Fatal(err)
	}
	if got["power"].(float64) != 1234.5 || got["energy_kwh"].(float64) != 12.25 {
		t.Fatalf("energy fields lost: %s", ev.Data)
	}
	// Canonical form is compact single-line JSON safe for ClickHouse String.
	if bytes.Contains(ev.Data, []byte{'\n'}) || ev.Data[0] != '{' {
		t.Fatalf("data not canonical: %q", ev.Data)
	}
}

func TestCanonicalDataRejectsUnstorableAndNonObjects(t *testing.T) {
	cases := []struct {
		name, data, code string
	}{
		{"array", `[1,2]`, CodeInvalidPayload},
		{"string", `"x"`, CodeInvalidPayload},
		{"nul", `{"v":"a\u0000b"}`, CodeInvalidPayload},
		{"trailing", `{"a":1}{"b":2}`, CodeInvalidPayload},
		{"broken", `{"a":`, CodeInvalidPayload},
	}
	for _, c := range cases {
		_, rej := CanonicalData([]byte(c.data), DefaultLimits().MaxPayloadBytes)
		if rej == nil || rej.Code != c.code {
			t.Errorf("%s: want %s, got %v", c.name, c.code, rej)
		}
	}
}

func TestNormalizeRejectsUnstorableWithoutFailingSiblingSemantics(t *testing.T) {
	// One bad message is rejected; a good energy reading in the same conceptual
	// batch is accepted (HTTP handler loops Normalize per message).
	now := time.Now()
	bad := Message{Stream: "energy.metrics", Data: json.RawMessage(`{"note":"a\u0000b"}`)}
	good := Message{Stream: "energy.metrics", Data: json.RawMessage(`{"power":100,"energy_kwh":0.5}`)}
	if _, rej := Normalize(&bad, ident(), now, DefaultLimits()); rej == nil || rej.Code != CodeInvalidPayload {
		t.Fatalf("bad: %v", rej)
	}
	ev, rej := Normalize(&good, ident(), now, DefaultLimits())
	if rej != nil || ev == nil {
		t.Fatalf("good: %v", rej)
	}
}
