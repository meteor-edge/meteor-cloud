package doctor

import (
	"errors"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/identity"
)

var now = time.Date(2026, 11, 22, 12, 0, 0, 0, time.UTC)

func healthy() Facts {
	return Facts{
		Version: "1.0.0", Now: now, State: identity.StateEnrolled, Server: "https://meteor.example.com",
		ServerChecked: true, LastHeartbeat: now.Add(-30 * time.Second), HeartbeatSeconds: 60,
		MQTTConfigured: true, MQTT: &MQTTState{Connected: true, Broker: "mqtt.example.com:8883"},
		HelperChecked: true, DiskFree: 2 << 30,
		Modules: map[string]api.ModuleHealth{"heartbeat": {State: "running"}, "deploy": {State: "running"}},
	}
}

func level(r Report, name string) Level {
	for _, c := range r.Checks {
		if c.Name == name {
			return c.Level
		}
	}
	return "missing"
}

func TestDiagnose(t *testing.T) {
	boom := "panic: boom"
	cases := []struct {
		name   string
		change func(*Facts)
		check  string
		want   Level
		ok     bool
	}{
		{"healthy", func(*Facts) {}, "server", OK, true},
		{"no server", func(f *Facts) { f.Server = "" }, "server", Fail, false},
		{"server down", func(f *Facts) { f.ServerErr = errors.New("dial tcp: refused") }, "server", Fail, false},
		{"pending approval", func(f *Facts) { f.State = identity.StatePending }, "enrollment", Warn, true},
		{"token rejected", func(f *Facts) { f.State = identity.StateRejected }, "enrollment", Fail, false},
		{"no heartbeat yet", func(f *Facts) { f.LastHeartbeat = time.Time{} }, "heartbeat", Warn, true},
		{"late heartbeat", func(f *Facts) { f.LastHeartbeat = now.Add(-10 * time.Minute) }, "heartbeat", Warn, true},
		{"mqtt down", func(f *Facts) { f.MQTT = &MQTTState{LastError: "tls: bad certificate"} }, "mqtt", Fail, false},
		{"mqtt not configured", func(f *Facts) { f.MQTTConfigured = false }, "mqtt", "missing", true},
		{"helper down", func(f *Facts) { f.HelperErr = errors.New("connection refused") }, "root helper", Fail, false},
		{"low disk", func(f *Facts) { f.DiskFree = 10 << 20 }, "disk", Fail, false},
		{"disk unknown", func(f *Facts) { f.DiskErr = errors.New("statfs") }, "disk", Warn, true},
		{"parked module", func(f *Facts) {
			f.Modules["deploy"] = api.ModuleHealth{State: "parked", Restarts: 5, LastError: &boom}
		}, "module deploy", Fail, false},
		{"failed module", func(f *Facts) { f.Modules["deploy"] = api.ModuleHealth{State: "failed", Restarts: 1} }, "module deploy", Warn, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := healthy()
			tc.change(&f)
			r := Diagnose(f)
			if got := level(r, tc.check); got != tc.want {
				t.Errorf("%s = %s, want %s\n%s", tc.check, got, tc.want, r.Text())
			}
			if r.OK() != tc.ok {
				t.Errorf("OK = %v, want %v", r.OK(), tc.ok)
			}
		})
	}
}

func TestNotEnrolledSkipsHeartbeatAndMQTT(t *testing.T) {
	f := healthy()
	f.State = identity.StateWaiting
	r := Diagnose(f)
	if level(r, "heartbeat") != "missing" || level(r, "mqtt") != "missing" {
		t.Errorf("heartbeat/mqtt checked while not enrolled:\n%s", r.Text())
	}
	if level(r, "enrollment") != Fail {
		t.Errorf("enrollment = %s", level(r, "enrollment"))
	}
}

func TestTextListsParkedFirstWithHint(t *testing.T) {
	f := healthy()
	boom := "panic: boom"
	f.Modules["deploy"] = api.ModuleHealth{State: "parked", Restarts: 5, LastError: &boom}
	text := Diagnose(f).Text()
	parked := strings.Index(text, "module deploy")
	running := strings.Index(text, "module heartbeat")
	if parked < 0 || running < 0 || parked > running {
		t.Errorf("parked module not listed first:\n%s", text)
	}
	for _, want := range []string{"5 restarts: panic: boom", "meteorcli module restart deploy", "Problems found"} {
		if !strings.Contains(text, want) {
			t.Errorf("text lacks %q:\n%s", want, text)
		}
	}
}

func TestTextIsCut(t *testing.T) {
	f := healthy()
	long := strings.Repeat("x", 400)
	for i := range 100 {
		f.Modules[strings.Repeat("m", i%60+1)+string(rune('a'+i%26))] = api.ModuleHealth{State: "failed", LastError: &long}
	}
	if n := len(Diagnose(f).Text()); n > MaxText {
		t.Errorf("text is %d bytes, max %d", n, MaxText)
	}
}
