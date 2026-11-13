package dispatch

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

var t0 = time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)

func enqueue(t *testing.T, s *state.Memory, transition, typ string, action dsl.Action) {
	t.Helper()
	action.Type = typ
	p := engine.ActionPayload{RuleID: "r1", RuleVersion: 3, RuleName: "High CPU", OrganizationID: "org-1", DeviceID: "dev-1",
		Severity: "critical", Transition: engine.Transition{Kind: transition, Episode: 4, At: t0,
			Values: map[string]float64{"cpu_percent": 97.5}}, Action: action}
	raw, _ := json.Marshal(p)
	key := "r1:dev-1:4:" + transition + ":a0:" + typ
	if errs := s.CommitMany(context.Background(), []*state.Batch{{Outbox: []state.OutboxItem{{Key: key, OrgID: "org-1",
		RuleID: "r1", RuleVersion: 3, DeviceID: "dev-1", Transition: transition, ActionType: typ, Payload: raw,
		NextAttempt: t0, CreatedAt: t0}}}}); errs[0] != nil {
		t.Fatal(errs[0])
	}
}

func statuses(s *state.Memory) []string {
	var out []string
	for _, o := range s.Outbox() {
		out = append(out, o.Status)
	}
	return out
}

func TestAlertsAreDeliveredInOrderWithRetriesAndDeadLetters(t *testing.T) {
	var mu sync.Mutex
	var got []map[string]any
	fail := atomic.Int32{}
	fail.Store(1)
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Data-Plane-Internal-Token") != "tok" || r.URL.Path != "/internal/rules/alert-transitions" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if fail.Add(-1) >= 0 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		var body struct{ Items []map[string]any }
		json.NewDecoder(r.Body).Decode(&body)
		mu.Lock()
		got = append(got, body.Items...)
		mu.Unlock()
		w.Write([]byte(`{"results":[{"status":"applied"}]}`))
	}))
	defer cp.Close()
	store := state.NewMemory()
	clock := engine.NewFakeClock(t0)
	d := New(Config{Backoff: []time.Duration{10 * time.Second}, MaxAttempts: 3}, store,
		map[string]Sender{dsl.ActionAlert: &AlertSender{CP: &ControlPlane{URL: cp.URL, Token: "tok"}}}, clock, nil, nil)
	enqueue(t, store, "firing", dsl.ActionAlert, dsl.Action{})
	enqueue(t, store, "resolved", dsl.ActionAlert, dsl.Action{})
	ctx := context.Background()

	if n, _ := d.Pass(ctx); n != 1 {
		t.Fatalf("only the firing is claimed while it is pending: %d", n)
	}
	if st := statuses(store); st[0] != "pending" || store.Outbox()[0].LastError == "" {
		t.Fatalf("503 is retried: %v", st)
	}
	if n, _ := d.Pass(ctx); n != 0 {
		t.Fatal("nothing due before the backoff")
	}
	clock.Advance(12 * time.Second)
	if n, _ := d.Pass(ctx); n != 1 {
		t.Fatal("firing retried")
	}
	if n, _ := d.Pass(ctx); n != 1 {
		t.Fatal("then the resolve")
	}
	if st := statuses(store); st[0] != "delivered" || st[1] != "delivered" {
		t.Fatalf("both delivered: %v", st)
	}
	if got[0]["idempotency_key"] != "r1:dev-1:4:firing:a0:alert" || got[1]["event"].(map[string]any)["transition"] != "resolved" {
		t.Fatalf("payload with key, in order: %v", got)
	}

	// A permanent refusal is dead-lettered at once; exhausting attempts too.
	refuse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusUnprocessableEntity) }))
	defer refuse.Close()
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusBadGateway) }))
	defer down.Close()
	store = state.NewMemory()
	d = New(Config{Backoff: []time.Duration{time.Second}, MaxAttempts: 2}, store, map[string]Sender{
		dsl.ActionAlert:         &AlertSender{CP: &ControlPlane{URL: refuse.URL}},
		dsl.ActionPlatformEvent: &PlatformEventSender{CP: &ControlPlane{URL: down.URL}},
	}, clock, nil, nil)
	enqueue(t, store, "firing", dsl.ActionAlert, dsl.Action{})
	enqueue(t, store, "firing", dsl.ActionPlatformEvent, dsl.Action{})
	enqueue(t, store, "firing", "unknown", dsl.Action{})
	d.Pass(ctx)
	clock.Advance(2 * time.Second)
	d.Pass(ctx)
	if st := statuses(store); st[0] != "dead" || st[1] != "dead" || st[2] != "dead" {
		t.Fatalf("422, exhausted retries and unknown type are dead: %v", st)
	}
}

func TestWebhookIsSignedSkippedWhenSilencedAndRefusesPrivateAddresses(t *testing.T) {
	var gotBody []byte
	var gotSig, gotEvent string
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotBody, _ = io.ReadAll(r.Body)
		gotSig, gotEvent = r.Header.Get(SignatureHeader), r.Header.Get("X-MeteorCloud-Event")
	}))
	defer hook.Close()
	delivery := &Delivery{URL: hook.URL, Kind: "webhook", Secret: "0123456789abcdef", Enabled: true}
	resolver := resolverFunc(func() (*Delivery, error) { return delivery, nil })
	store := state.NewMemory()
	clock := engine.NewFakeClock(t0)
	sender := &WebhookSender{Resolver: resolver, Client: NewWebhookClient(true), Now: func() time.Time { return t0 }}
	d := New(Config{}, store, map[string]Sender{dsl.ActionWebhook: sender, dsl.ActionNotification: sender}, clock, nil, nil)
	ctx := context.Background()

	enqueue(t, store, "firing", dsl.ActionWebhook, dsl.Action{IntegrationID: "i1", Params: json.RawMessage(`{"team":"ops"}`)})
	d.Pass(ctx)
	if st := statuses(store); st[0] != "delivered" {
		t.Fatalf("delivered: %v %s", st, store.Outbox()[0].LastError)
	}
	mac := hmac.New(sha256.New, []byte(delivery.Secret))
	mac.Write([]byte("1794571200."))
	mac.Write(gotBody)
	if gotSig != "t=1794571200,v1="+hex.EncodeToString(mac.Sum(nil)) || gotEvent != "rule.firing" {
		t.Fatalf("signature %q event %q", gotSig, gotEvent)
	}
	var body map[string]any
	json.Unmarshal(gotBody, &body)
	if body["rule_name"] != "High CPU" || body["params"].(map[string]any)["team"] != "ops" {
		t.Fatalf("body %s", gotBody)
	}

	delivery.Kind = "slack"
	enqueue(t, store, "resolved", dsl.ActionNotification, dsl.Action{IntegrationID: "i1"})
	d.Pass(ctx)
	if !strings.Contains(string(gotBody), `"text":"[CRITICAL] High CPU resolved on device dev-1: cpu_percent=97.5"`) {
		t.Fatalf("slack text %s", gotBody)
	}

	delivery.Silenced = true
	enqueue(t, store, "firing", dsl.ActionNotification, dsl.Action{IntegrationID: "i1"})
	gotBody = nil
	d.Pass(ctx)
	if gotBody != nil || statuses(store)[2] != "delivered" {
		t.Fatal("a silenced notification is skipped, not retried")
	}

	// The default client refuses loopback (httptest listens on 127.0.0.1).
	delivery.Silenced = false
	sender.Client = NewWebhookClient(false)
	enqueue(t, store, "resolved", dsl.ActionWebhook, dsl.Action{IntegrationID: "i1"})
	d.Pass(ctx)
	last := store.Outbox()[3]
	if last.Status != "dead" || !strings.Contains(last.LastError, "not public") {
		t.Fatalf("private address refused permanently: %+v", last)
	}
}

func TestBlockedAddresses(t *testing.T) {
	for _, tc := range []struct {
		ip      string
		blocked bool
	}{{"127.0.0.1", true}, {"10.1.2.3", true}, {"192.168.1.1", true}, {"169.254.169.254", true}, {"100.64.0.1", true},
		{"::1", true}, {"fd00::1", true}, {"fe80::1", true}, {"::ffff:10.0.0.1", true}, {"0.0.0.0", true},
		{"93.184.216.34", false}, {"2606:4700::1111", false}} {
		if got := blocked(mustAddr(tc.ip)); got != tc.blocked {
			t.Errorf("%s blocked=%v", tc.ip, got)
		}
	}
}
