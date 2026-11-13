package authz

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

const (
	devA = "device_11111111-1111-1111-1111-111111111111"
	devB = "device_22222222-2222-2222-2222-222222222222"
)

// fakeControlPlane mimics the control plane policy closely enough for the engine.
type fakeControlPlane struct {
	authCalls, aclCalls atomic.Int64
	down                atomic.Bool
	revoked             atomic.Bool
}

func (f *fakeControlPlane) handler(t *testing.T) http.Handler {
	mux := http.NewServeMux()
	guard := func(w http.ResponseWriter, r *http.Request) bool {
		if f.down.Load() {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return false
		}
		if r.Header.Get("X-MQTT-Internal-Token") != "secret" {
			http.Error(w, "forbidden", http.StatusUnauthorized)
			return false
		}
		return true
	}
	mux.HandleFunc("/internal/mqtt/authenticate", func(w http.ResponseWriter, r *http.Request) {
		f.authCalls.Add(1)
		if !guard(w, r) {
			return
		}
		var req authRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		resp := authResponse{Result: "deny"}
		switch {
		case req.Username == "platform" && req.Password == "platform-pw":
			resp = authResponse{Result: "allow", IsSuperuser: true}
		case req.Username == devA && req.Password == "pw-a" && !f.revoked.Load():
			resp = authResponse{Result: "allow"}
		}
		_ = json.NewEncoder(w).Encode(resp)
	})
	mux.HandleFunc("/internal/mqtt/authorize", func(w http.ResponseWriter, r *http.Request) {
		f.aclCalls.Add(1)
		if !guard(w, r) {
			return
		}
		var req aclRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		own := "devices/" + req.Username[len("device_"):] + "/"
		allowed := (req.Action == "publish" && req.Topic == own+"status") ||
			(req.Action == "publish" && req.Topic == own+"metrics" && !req.Retain) ||
			((req.Action == "subscribe" || req.Action == "read") && req.Topic == own+"commands")
		resp := aclResponse{Result: "deny"}
		if allowed {
			resp.Result = "allow"
		}
		_ = json.NewEncoder(w).Encode(resp)
	})
	return mux
}

func newEngine(t *testing.T, mutate func(*Config)) (*Engine, *fakeControlPlane) {
	t.Helper()
	fake := &fakeControlPlane{}
	srv := httptest.NewServer(fake.handler(t))
	t.Cleanup(srv.Close)
	cfg := Config{ControlPlaneURL: srv.URL + "/", InternalToken: "secret", AllowTTL: time.Minute, DenyTTL: 10 * time.Second}
	if mutate != nil {
		mutate(&cfg)
	}
	engine, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return engine, fake
}

func TestNewRequiresURLAndToken(t *testing.T) {
	if _, err := New(Config{InternalToken: "x"}); err == nil {
		t.Fatal("missing URL must fail")
	}
	if _, err := New(Config{ControlPlaneURL: "http://x"}); err == nil {
		t.Fatal("missing token must fail")
	}
}

func TestAuthenticate(t *testing.T) {
	e, fake := newEngine(t, nil)
	if ok, err := e.Authenticate("device-a", devA, "pw-a"); !ok || err != nil {
		t.Fatalf("valid login denied: %v", err)
	}
	if ok, _ := e.Authenticate("device-a", devA, "wrong"); ok {
		t.Fatal("wrong password accepted")
	}
	if ok, _ := e.Authenticate("x", "", "pw"); ok {
		t.Fatal("empty username accepted")
	}
	// Logins are never cached.
	e.Authenticate("device-a", devA, "pw-a")
	if got := fake.authCalls.Load(); got != 3 {
		t.Fatalf("expected 3 auth calls, got %d", got)
	}
}

func TestWrongInternalTokenFailsClosed(t *testing.T) {
	e, _ := newEngine(t, func(c *Config) { c.InternalToken = "nope" })
	if ok, err := e.Authenticate("device-a", devA, "pw-a"); ok || err == nil {
		t.Fatal("a rejected internal token must deny with an error")
	}
}

func TestDeviceCannotTouchOtherDevice(t *testing.T) {
	e, _ := newEngine(t, nil)
	cases := []struct {
		access int
		topic  string
		retain bool
		want   bool
	}{
		{AccessWrite, "devices/11111111-1111-1111-1111-111111111111/status", true, true},
		{AccessWrite, "devices/11111111-1111-1111-1111-111111111111/metrics", false, true},
		{AccessWrite, "devices/11111111-1111-1111-1111-111111111111/metrics", true, false},
		{AccessSubscribe, "devices/11111111-1111-1111-1111-111111111111/commands", false, true},
		{AccessRead, "devices/11111111-1111-1111-1111-111111111111/commands", false, true},
		{AccessWrite, "devices/11111111-1111-1111-1111-111111111111/commands", false, false},
		{AccessWrite, "devices/22222222-2222-2222-2222-222222222222/commands", false, false},
		{AccessSubscribe, "devices/22222222-2222-2222-2222-222222222222/commands", false, false},
		{AccessRead, "devices/22222222-2222-2222-2222-222222222222/commands", false, false},
		{AccessSubscribe, "devices/#", false, false},
		{AccessWrite, DefaultKickTopic, false, false},
		{AccessUnsubscribe, "devices/#", false, true},
		{99, "devices/11111111-1111-1111-1111-111111111111/status", false, false},
	}
	for _, c := range cases {
		got, err := e.Authorize("device-a", devA, c.access, c.topic, c.retain)
		if err != nil || got != c.want {
			t.Errorf("access=%d topic=%s retain=%v: got %v (err %v), want %v", c.access, c.topic, c.retain, got, err, c.want)
		}
	}
}

func TestDecisionsAreCachedAndExpire(t *testing.T) {
	now := time.Unix(1000, 0)
	e, fake := newEngine(t, func(c *Config) { c.Now = func() time.Time { return now } })
	topic := "devices/11111111-1111-1111-1111-111111111111/status"
	for i := 0; i < 5; i++ {
		e.Authorize("device-a", devA, AccessWrite, topic, false)
	}
	if got := fake.aclCalls.Load(); got != 1 {
		t.Fatalf("expected 1 authorize call, got %d", got)
	}
	now = now.Add(2 * time.Minute)
	e.Authorize("device-a", devA, AccessWrite, topic, false)
	if got := fake.aclCalls.Load(); got != 2 {
		t.Fatalf("expected a refresh after expiry, got %d calls", got)
	}
}

func TestControlPlaneDownFailsClosedAndIsNotCached(t *testing.T) {
	e, fake := newEngine(t, nil)
	topic := "devices/11111111-1111-1111-1111-111111111111/status"
	fake.down.Store(true)
	if ok, err := e.Authorize("device-a", devA, AccessWrite, topic, false); ok || err == nil {
		t.Fatal("must deny with an error while the control plane is down")
	}
	if ok, _ := e.Authenticate("device-a", devA, "pw-a"); ok {
		t.Fatal("login must be denied while the control plane is down")
	}
	fake.down.Store(false)
	if ok, err := e.Authorize("device-a", devA, AccessWrite, topic, false); !ok || err != nil {
		t.Fatal("must recover as soon as the control plane is back")
	}
}

func TestSuperuserSkipsTopicChecks(t *testing.T) {
	e, fake := newEngine(t, nil)
	if ok, _ := e.Authorize("dp", "platform", AccessSubscribe, "devices/#", false); ok {
		t.Fatal("platform is not a superuser before it logged in")
	}
	before := fake.aclCalls.Load()
	if ok, _ := e.Authenticate("dp", "platform", "platform-pw"); !ok {
		t.Fatal("platform login failed")
	}
	if ok, _ := e.Authorize("dp", "platform", AccessSubscribe, "$share/g/devices/#", false); !ok {
		t.Fatal("superuser denied")
	}
	if fake.aclCalls.Load() != before {
		t.Fatal("superuser checks must not call the control plane")
	}
	// A failed platform login drops the shortcut.
	e.Authenticate("dp", "platform", "wrong")
	if ok, _ := e.Authorize("dp", "platform", AccessSubscribe, "devices/#", false); ok {
		t.Fatal("shortcut must be dropped after a failed login")
	}
}

func TestKickOnlyFromSuperuser(t *testing.T) {
	e, fake := newEngine(t, nil)
	payload := []byte(`{"username":"` + devA + `"}`)
	e.Authenticate("device-b", devB, "x") // not a superuser
	if _, ok := e.KickTarget(devB, DefaultKickTopic, payload); ok {
		t.Fatal("a device must not be able to disconnect another device")
	}
	e.Authenticate("dp", "platform", "platform-pw")
	if _, ok := e.KickTarget("platform", "devices/x/status", payload); ok {
		t.Fatal("only the control topic kicks")
	}
	if _, ok := e.KickTarget("platform", DefaultKickTopic, []byte("not json")); ok {
		t.Fatal("bad payload must be ignored")
	}
	if _, ok := e.KickTarget("platform", DefaultKickTopic, []byte(`{"username":"platform"}`)); ok {
		t.Fatal("the platform must not disconnect itself")
	}

	topic := "devices/11111111-1111-1111-1111-111111111111/status"
	e.Authorize("device-a", devA, AccessWrite, topic, false) // cached allow
	target, ok := e.KickTarget("platform", DefaultKickTopic, payload)
	if !ok || target != devA {
		t.Fatalf("expected kick of %s, got %q %v", devA, target, ok)
	}
	calls := fake.aclCalls.Load()
	e.Authorize("device-a", devA, AccessWrite, topic, false)
	if fake.aclCalls.Load() != calls+1 {
		t.Fatal("kick must drop the cached decisions for that user")
	}
	fake.revoked.Store(true)
	if ok, _ := e.Authenticate("device-a", devA, "pw-a"); ok {
		t.Fatal("revoked device must not log back in")
	}
}

func TestCacheIsBounded(t *testing.T) {
	e, _ := newEngine(t, func(c *Config) { c.MaxCacheEntries = 3 })
	for _, topic := range []string{"a", "b", "c", "d", "e"} {
		e.Authorize("device-a", devA, AccessWrite, topic, false)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if len(e.cache) > 3 {
		t.Fatalf("cache grew to %d entries", len(e.cache))
	}
}
