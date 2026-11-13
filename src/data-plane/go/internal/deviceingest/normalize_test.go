package deviceingest

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/envelope"
)

const (
	devA = "11111111-1111-4111-8111-111111111111"
	devB = "22222222-2222-4222-8222-222222222222"
	orgA = "aaaaaaaa-0000-4000-8000-00000000000a"
	orgB = "bbbbbbbb-0000-4000-8000-00000000000b"
)

var now = time.Date(2026, 11, 12, 12, 0, 0, 0, time.UTC)

func TestParseTopic(t *testing.T) {
	cases := []struct {
		topic string
		ok    bool
		ch    Channel
	}{
		{"devices/" + devA + "/metrics", true, ChannelMetrics},
		{"devices/" + devA + "/status", true, ChannelStatus},
		{"devices/" + devA + "/events", true, ChannelEvents},
		{"devices/" + strings.ToUpper(devA) + "/metrics", true, ChannelMetrics},
		{"devices/" + devA + "/commands", false, ""},
		{"devices/" + devA + "/commands/result", false, ""},
		{"devices/not-a-uuid/metrics", false, ""},
		{"devices/+/metrics", false, ""},
		{"devices/a.b/metrics", false, ""},
		{"meteor/v1/orgs/x/apps/y/devices/z/telemetry/t", false, ""},
	}
	for _, c := range cases {
		id, ch, ok := ParseTopic(c.topic)
		if ok != c.ok || ch != c.ch {
			t.Errorf("%s: got %q %v", c.topic, ch, ok)
		}
		if ok && id != devA {
			t.Errorf("%s: device id %q is not canonical", c.topic, id)
		}
	}
}

func TestSubjects(t *testing.T) {
	if s := Subject(ChannelMetrics, orgA, devA); s != "telemetry.v1."+orgA+"."+devA {
		t.Fatal(s)
	}
	if s := Subject(ChannelStatus, orgA, devA); s != "device.status.v1."+orgA+"."+devA {
		t.Fatal(s)
	}
	if s := Subject(ChannelEvents, orgA, devA); s != "device.events.v1."+orgA+"."+devA {
		t.Fatal(s)
	}
}

func TestNormalizeMetrics(t *testing.T) {
	payload := `{"cpu_percent":35.2,"memory_percent":87.5,"temperature_c":54.1,"timestamp":"2026-11-12T11:59:58Z"}`
	res, rej := Normalize(devA, orgA, ChannelMetrics, []byte(payload), now, envelope.DefaultLimits())
	if rej != nil {
		t.Fatal(rej)
	}
	ev := res.Event
	if ev.Kind != envelope.KindTelemetry || ev.Stream != StreamMetrics || ev.SchemaVersion != 1 {
		t.Fatalf("envelope: %+v", ev)
	}
	if ev.OrganizationID != orgA || ev.DeviceID != devA {
		t.Fatalf("identity: %+v", ev)
	}
	if !ev.Time.Equal(time.Date(2026, 11, 12, 11, 59, 58, 0, time.UTC)) || !ev.ReceivedAt.Equal(now) {
		t.Fatalf("time: %v %v", ev.Time, ev.ReceivedAt)
	}
	var data map[string]any
	json.Unmarshal(ev.Data, &data)
	if data["cpu_percent"] != 35.2 || data["timestamp"] != nil {
		t.Fatalf("data: %s", ev.Data)
	}
	// Same message again (an MQTT redelivery) gets the same id; another one does not.
	again, _ := Normalize(devA, orgA, ChannelMetrics, []byte(payload), now.Add(time.Second), envelope.DefaultLimits())
	if again.Event.ID != ev.ID {
		t.Fatalf("redelivery got a new id: %s vs %s", again.Event.ID, ev.ID)
	}
	other, _ := Normalize(devB, orgA, ChannelMetrics, []byte(payload), now, envelope.DefaultLimits())
	if other.Event.ID == ev.ID {
		t.Fatal("two devices produced the same id")
	}
}

func TestNormalizeIDs(t *testing.T) {
	res, rej := Normalize(devA, orgA, ChannelEvents, []byte(`{"event_id":"evt-1","type":"door.opened"}`), now, envelope.DefaultLimits())
	if rej != nil || res.Event.ID != "evt-1" || res.Event.Type != "door.opened" || res.Event.Kind != envelope.KindEvent {
		t.Fatalf("%+v %v", res, rej)
	}
	a, _ := Normalize(devA, orgA, ChannelStatus, []byte(`{"status":"offline"}`), now, envelope.DefaultLimits())
	b, _ := Normalize(devA, orgA, ChannelStatus, []byte(`{"status":"offline"}`), now, envelope.DefaultLimits())
	if a.Event.ID == b.Event.ID {
		t.Fatal("messages without timestamp or id must get distinct ids")
	}
}

func TestNormalizeRejectsInvalidMessages(t *testing.T) {
	lim := envelope.DefaultLimits()
	lim.MaxPayloadBytes = 64
	cases := []struct {
		name, payload, code string
	}{
		{"not json", `cpu=1`, ReasonInvalidPayload},
		{"array", `[1,2]`, ReasonInvalidPayload},
		{"truncated", `{"cpu":`, ReasonInvalidPayload},
		{"empty", ``, ReasonInvalidPayload},
		{"too large", `{"x":"` + strings.Repeat("a", 100) + `"}`, ReasonTooLarge},
		{"bad id", `{"event_id":"has space"}`, ReasonInvalidID},
		{"non-string id", `{"event_id":5}`, ReasonInvalidID},
	}
	for _, c := range cases {
		_, rej := Normalize(devA, orgA, ChannelMetrics, []byte(c.payload), now, lim)
		if rej == nil || rej.Code != c.code {
			t.Errorf("%s: got %v, want %s", c.name, rej, c.code)
		}
	}
	if _, rej := Normalize("a.b", orgA, ChannelMetrics, []byte(`{}`), now, lim); rej == nil {
		t.Error("a device id with a dot must be refused")
	}
	if _, rej := Normalize(devA, "org.>", ChannelMetrics, []byte(`{}`), now, lim); rej == nil {
		t.Error("an organization id with wildcards must be refused")
	}
}

func TestNormalizeReplacesUnusableDeviceTime(t *testing.T) {
	for _, ts := range []string{"1970-01-01T00:00:10Z", "2027-01-01T00:00:00Z"} {
		res, rej := Normalize(devA, orgA, ChannelMetrics, []byte(`{"timestamp":"`+ts+`"}`), now, envelope.DefaultLimits())
		if rej != nil || !res.TimeReplaced || !res.Event.Time.Equal(now) {
			t.Errorf("%s: %+v %v", ts, res, rej)
		}
	}
}

func TestPayloadIdentityIsIgnored(t *testing.T) {
	// A device in org A claims org B and device B in its payload.
	payload := `{"organization_id":"` + orgB + `","device_id":"` + devB + `","cpu_percent":1}`
	res, rej := Normalize(devA, orgA, ChannelMetrics, []byte(payload), now, envelope.DefaultLimits())
	if rej != nil {
		t.Fatal(rej)
	}
	if res.Event.OrganizationID != orgA || res.Event.DeviceID != devA {
		t.Fatalf("payload identity leaked into the envelope: %+v", res.Event)
	}
	if s := Subject(ChannelMetrics, res.Event.OrganizationID, res.Event.DeviceID); !strings.Contains(s, orgA) || strings.Contains(s, orgB) {
		t.Fatalf("subject %s", s)
	}
}
