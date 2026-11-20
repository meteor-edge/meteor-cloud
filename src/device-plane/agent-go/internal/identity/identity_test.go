package identity

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/fakecp"
)

const (
	routeRegister = "/api/v1/agent/register"
	routeRequest  = "/api/v1/agent/enroll/request"
	routePoll     = "/api/v1/agent/enroll/poll"
	routeMQTT     = "/api/v1/agent/mqtt"
	routeCreds    = "/api/v1/agent/mqtt/credentials"
)

// rig is one Service against a fake control plane in a scratch root.
type rig struct {
	t      *testing.T
	p      config.Paths
	cp     *fakecp.Server
	svc    *Service
	events struct {
		enrolled, rotated, config atomic.Int64
	}
}

func newRig(t *testing.T, configured bool) *rig {
	t.Helper()
	r := &rig{t: t, p: config.DefaultPaths(t.TempDir()), cp: fakecp.New(t)}
	if err := os.MkdirAll(r.p.IdentityDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if configured {
		r.writeConfig(r.cp.URL)
	}
	client, err := api.New(api.Options{Version: "1.2.3", HTTPClient: r.cp.Client()})
	if err != nil {
		t.Fatal(err)
	}
	r.svc = &Service{
		Source: NewSource(r.p), API: client, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Inventory: func() api.Inventory { return api.Inventory{"hostname": "edge"} },
		MinPoll:   20 * time.Millisecond, RetryWait: 40 * time.Millisecond,
		AuthRetryMin: 30 * time.Millisecond, AuthRetryMax: 100 * time.Millisecond, Tick: 50 * time.Millisecond,
		Events: Events{
			Enrolled:           func() { r.events.enrolled.Add(1) },
			CredentialsRotated: func() { r.events.rotated.Add(1) },
			ConfigChanged:      func() { r.events.config.Add(1) },
		},
	}
	r.svc.Reload(context.Background())
	return r
}

func (r *rig) writeConfig(server string) {
	r.t.Helper()
	if err := (&config.Config{APIBase: server}).Save(r.p.ConfigFile, config.Owner{}); err != nil {
		r.t.Fatal(err)
	}
}

func (r *rig) write(path, content string) {
	r.t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		r.t.Fatal(err)
	}
}

// run starts Service.Run until the test ends.
func (r *rig) run() {
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := r.svc.Run(ctx); err != nil {
			r.t.Errorf("Run: %v", err)
		}
	}()
	r.t.Cleanup(func() { cancel(); wg.Wait() })
}

func eventually(t *testing.T, what string, ok func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !ok() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func approved(id, token string, mqtt map[string]any) map[string]any {
	return map[string]any{
		"status": "approved", "poll_interval_seconds": 10, "device_id": id, "device_token": token,
		"organization_id": "org-1", "name": "edge", "heartbeat_interval_seconds": 30, "mqtt": mqtt,
	}
}

func registered(id, token string, mqtt map[string]any) map[string]any {
	return map[string]any{
		"device_id": id, "device_token": token, "organization_id": "org-1", "name": "edge",
		"heartbeat_interval_seconds": 30, "mqtt": mqtt,
	}
}

func codeOf(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

func TestRegisterStoresIdentityAndProvesHardwareOnReRegister(t *testing.T) {
	r := newRig(t, true)
	ca := "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n"
	mqtt := map[string]any{"host": "broker", "port": 8883, "username": "u", "password": "p1", "tls": true, "ca_cert": ca}
	r.cp.On("POST", routeRegister, fakecp.Created(registered("d-1", "dev_one", mqtt)), fakecp.Created(registered("d-1", "dev_two", nil)))
	r.write(r.p.APIKeyFile(), "key_x")
	if err := os.MkdirAll(r.p.StateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	r.write(filepath.Join(r.p.StateDir, "signing-keys.json"), "{}")

	got, err := r.svc.Register(context.Background(), "reg_token", "edge")
	if err != nil || got.DeviceID != "d-1" || got.HeartbeatSeconds != 30 {
		t.Fatalf("Register = %+v, %v", got, err)
	}
	if tok, err := r.svc.Source.Token(); tok != "dev_one" || err != nil {
		t.Errorf("Token = %q, %v", tok, err)
	}
	if st, err := os.Stat(r.p.DeviceTokenFile()); err != nil || st.Mode().Perm() != 0o600 {
		t.Errorf("device-token: %v %v", st, err)
	}
	m, ok := r.svc.Source.MQTT()
	if !ok || m.Password != "p1" || m.CACertPath == nil || *m.CACertPath != r.p.MQTTCAFile() {
		t.Errorf("mqtt %+v %v", m, ok)
	}
	if data, _ := os.ReadFile(r.p.MQTTCAFile()); string(data) != ca {
		t.Errorf("CA file %q", data)
	}
	for _, gone := range []string{r.p.APIKeyFile(), filepath.Join(r.p.StateDir, "signing-keys.json"), r.p.StagingDir()} {
		if _, err := os.Stat(gone); err == nil {
			t.Errorf("%s still exists", gone)
		}
	}
	if r.events.enrolled.Load() != 1 {
		t.Errorf("Enrolled events %d", r.events.enrolled.Load())
	}

	if _, err := r.svc.Register(context.Background(), "reg_token", ""); err != nil {
		t.Fatal(err)
	}
	calls := r.cp.Calls("POST", routeRegister)
	if calls[0].Body["token"] != "reg_token" || calls[0].Body["hostname"] != "edge" || calls[0].Body["name"] != "edge" {
		t.Errorf("first body %v", calls[0].Body)
	}
	if _, sent := calls[0].Body["device_token"]; sent {
		t.Error("first register sent a device token")
	}
	if calls[1].Body["device_token"] != "dev_one" {
		t.Errorf("re-register body %v", calls[1].Body)
	}
	// A register without MQTT keeps the stored broker settings (as Python did).
	if m, ok := r.svc.Source.MQTT(); !ok || m.Password != "p1" {
		t.Errorf("mqtt after re-register %+v", m)
	}
	if tok, _ := r.svc.Source.Token(); tok != "dev_two" {
		t.Errorf("token after re-register %q", tok)
	}
}

func TestRegisterErrors(t *testing.T) {
	r := newRig(t, false)
	if _, err := r.svc.Register(context.Background(), "reg", ""); codeOf(err) != CodeNotConfigured {
		t.Errorf("unconfigured: %v", err)
	}
	r = newRig(t, true)
	r.cp.On("POST", routeRegister, fakecp.Fail(401, "invalid_token"), fakecp.Fail(503, "busy", "Retry-After", "7"),
		fakecp.Created(map[string]any{"device_id": "", "device_token": ""}))
	if _, err := r.svc.Register(context.Background(), "reg", ""); codeOf(err) != CodeKeyRejected {
		t.Errorf("401: %v", err)
	}
	_, err := r.svc.Register(context.Background(), "reg", "")
	var e *Error
	if !errors.As(err, &e) || e.Code != CodeRetryLater || e.RetryAfter != 7*time.Second {
		t.Errorf("503: %#v", err)
	}
	if _, err := r.svc.Register(context.Background(), "reg", ""); codeOf(err) != CodeServerError {
		t.Errorf("empty answer: %v", err)
	}
	if r.svc.Source.Enrolled() {
		t.Error("enrolled after failures")
	}
}

func TestRequestTokenFlow(t *testing.T) {
	r := newRig(t, true)
	ctx := context.Background()
	if _, err := r.svc.RequestToken(ctx, RequestOptions{}); codeOf(err) != CodeNoAPIKey {
		t.Fatalf("no key: %v", err)
	}
	r.cp.On("POST", routeRequest,
		fakecp.Created(map[string]any{"request_id": "r-1", "claim_secret": "clm_1", "status": "pending", "poll_interval_seconds": 10}),
		fakecp.Created(map[string]any{"request_id": "r-2", "claim_secret": "clm_2", "status": "pending", "poll_interval_seconds": 10}))
	r.cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "pending", "poll_interval_seconds": 10}),
		fakecp.OK(map[string]any{"status": "expired"}), fakecp.OK(approved("d-9", "dev_9", nil)))

	res, err := r.svc.RequestToken(ctx, RequestOptions{APIKey: "key_x", Name: "edge-7"})
	if err != nil || res.Status != "submitted" || res.RequestID != "r-1" || res.PollIntervalSeconds != 10 {
		t.Fatalf("submit: %+v %v", res, err)
	}
	if data, _ := os.ReadFile(r.p.APIKeyFile()); string(data) != "key_x" {
		t.Errorf("api key file %q", data)
	}
	call := r.cp.Calls("POST", routeRequest)[0]
	if call.Header.Get("Authorization") != "Bearer key_x" || call.Body["name"] != "edge-7" || call.Body["hostname"] != "edge" {
		t.Errorf("request call %v %v", call.Header, call.Body)
	}
	if st := r.svc.Source.Status(); st.State != StatePending || st.PendingRequestID != "r-1" {
		t.Errorf("status %+v", st)
	}
	// Pending and not due for a poll yet: refused without asking the server.
	if _, err := r.svc.RequestToken(ctx, RequestOptions{}); codeOf(err) != CodeAlreadyPending {
		t.Errorf("request before the poll is due: %v", err)
	}
	if n := len(r.cp.Calls("POST", routePoll)); n != 0 {
		t.Errorf("polled %d times before the poll interval", n)
	}
	due := func() { r.svc.nextPoll = time.Time{} } // no Run here: skip the 10 s poll interval
	// Due, still pending: refuse a second request without --new.
	due()
	if _, err := r.svc.RequestToken(ctx, RequestOptions{}); codeOf(err) != CodeAlreadyPending {
		t.Errorf("second request: %v", err)
	}
	// The stored request expired: a new one is submitted with the stored name.
	due()
	res, err = r.svc.RequestToken(ctx, RequestOptions{})
	if err != nil || res.RequestID != "r-2" || res.Previous != PollExpired {
		t.Fatalf("after expiry: %+v %v", res, err)
	}
	if got := r.cp.Calls("POST", routeRequest)[1].Body["name"]; got != "edge-7" {
		t.Errorf("resubmitted name %v", got)
	}
	// Approved meanwhile: request-token reports the identity instead of submitting.
	due()
	res, err = r.svc.RequestToken(ctx, RequestOptions{})
	if err != nil || res.Status != PollApproved || res.Enrollment == nil || res.Enrollment.DeviceID != "d-9" {
		t.Fatalf("approved: %+v %v", res, err)
	}
	for _, p := range r.cp.Calls("POST", routePoll) {
		if p.Header.Get("Authorization") != "" || p.Body["claim_secret"] == nil {
			t.Errorf("poll %v %v", p.Header, p.Body)
		}
	}
	if _, err := os.Stat(r.p.PendingClaimFile()); err == nil {
		t.Error("pending claim kept after approval")
	}
	if _, err := r.svc.RequestToken(ctx, RequestOptions{APIKey: "key_y"}); codeOf(err) != CodeAlreadyEnrolled {
		t.Errorf("enrolled: %v", err)
	}
}

func TestRequestTokenErrorCodes(t *testing.T) {
	r := newRig(t, true)
	r.cp.On("POST", routeRequest, fakecp.Fail(401, "invalid_api_key"), fakecp.Fail(429, "rate_limited", "Retry-After", "42"))
	ctx := context.Background()
	if _, err := r.svc.RequestToken(ctx, RequestOptions{APIKey: "key_x"}); codeOf(err) != CodeKeyRejected {
		t.Errorf("401: %v", err)
	}
	_, err := r.svc.RequestToken(ctx, RequestOptions{})
	var e *Error
	if !errors.As(err, &e) || e.Code != CodeRetryLater || e.RetryAfter != 42*time.Second {
		t.Errorf("429: %#v", err)
	}
}

// A request submitted before a reboot is polled at start and, once approved,
// the device is enrolled with nobody running a command.
func TestPendingRequestCollectedAfterRestart(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1","name":"edge","extra":"kept"}`)
	r.cp.On("POST", routePoll,
		fakecp.OK(map[string]any{"status": "pending", "poll_interval_seconds": 0}),
		fakecp.Fail(429, "rate_limited", "Retry-After", "0"),
		fakecp.OK(approved("d-1", "dev_1", nil)))
	r.cp.On("GET", routeMQTT, fakecp.OK(map[string]any{"enabled": false, "config_version": nil}))
	r.svc = &Service{Source: NewSource(r.p), API: r.svc.API, Log: r.svc.Log, Events: r.svc.Events,
		MinPoll: 20 * time.Millisecond, RetryWait: 30 * time.Millisecond, Tick: 50 * time.Millisecond}
	r.run()

	eventually(t, "enrolled", r.svc.Source.Enrolled)
	if n := len(r.cp.Calls("POST", routePoll)); n != 3 {
		t.Errorf("polls %d, want 3", n)
	}
	calls := r.cp.Calls("POST", routePoll)
	for i := 1; i < len(calls); i++ {
		if gap := calls[i].At.Sub(calls[i-1].At); gap < 15*time.Millisecond {
			t.Errorf("poll %d came %s after the previous one; MinPoll is 20ms", i, gap)
		}
	}
	eventually(t, "Enrolled event", func() bool { return r.events.enrolled.Load() == 1 })
	// The MQTT settings are checked once enrolled; MQTT turned off is remembered.
	r.cp.Wait(t, "GET", routeMQTT, 1, 5*time.Second)
	eventually(t, "MQTT off", func() bool { _, ok := r.svc.Source.MQTT(); return !ok && r.events.rotated.Load() == 1 })
}

func TestBackgroundOutcomes(t *testing.T) {
	tests := []struct {
		name    string
		reply   fakecp.Reply
		apiKey  string
		outcome string
		wantErr string
	}{
		{"rejected", fakecp.OK(map[string]any{"status": "rejected", "rejection_reason": "not ours"}), "", PollRejected, "not ours"},
		{"unknown request", fakecp.Fail(404, "not_found"), "", PollGone, "does not know"},
		{"expired without a key", fakecp.OK(map[string]any{"status": "expired"}), "", PollExpired, "expired"},
		{"approved elsewhere", fakecp.OK(map[string]any{"status": "approved"}), "", PollRejected, "already collected"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := newRig(t, true)
			r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1"}`)
			r.cp.On("POST", routePoll, tt.reply)
			r.svc.Reload(context.Background())
			r.run()
			eventually(t, "request ended", func() bool { return r.svc.Source.Status().PendingRequestID == "" })
			st := r.svc.Source.Status()
			if st.Outcome == nil || st.Outcome.Status != tt.outcome {
				t.Fatalf("outcome %+v", st.Outcome)
			}
			_, err := r.svc.Claim(context.Background(), 0)
			if codeOf(err) != CodeRequestEnded || !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("Claim: %v", err)
			}
		})
	}
}

func TestExpiredRequestIsResubmittedWithTheSavedKey(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1","name":"edge-3"}`)
	r.write(r.p.APIKeyFile(), "key_x")
	r.cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "expired"}), fakecp.OK(map[string]any{"status": "pending"}))
	r.cp.On("POST", routeRequest, fakecp.Created(map[string]any{"request_id": "r-2", "claim_secret": "clm_2", "status": "pending"}))
	r.svc.Reload(context.Background())
	r.run()
	eventually(t, "new request", func() bool { return r.svc.Source.Status().PendingRequestID == "r-2" })
	if got := r.cp.Calls("POST", routeRequest)[0].Body["name"]; got != "edge-3" {
		t.Errorf("name %v", got)
	}
}

func TestAutoRequestAtStartWithSavedKey(t *testing.T) {
	r := newRig(t, false)
	if err := (&config.Config{APIBase: r.cp.URL, EnrollName: "from-meteorenv"}).Save(r.p.ConfigFile, config.Owner{}); err != nil {
		t.Fatal(err)
	}
	r.write(r.p.APIKeyFile(), "key_x")
	r.cp.On("POST", routeRequest, fakecp.Fail(503, "down"),
		fakecp.Created(map[string]any{"request_id": "r-1", "claim_secret": "clm_1", "status": "pending"}))
	r.svc.Reload(context.Background())
	// The first attempt fails (network down at boot); it is tried again.
	r.svc.nextAsk = time.Now()
	r.run()
	r.cp.Wait(t, "POST", routeRequest, 1, 5*time.Second)
	r.svc.mu.Lock()
	r.svc.nextAsk = time.Now() // skip the one-minute wait
	r.svc.mu.Unlock()
	r.svc.poke()
	eventually(t, "request stored", func() bool { return r.svc.Source.Status().PendingRequestID == "r-1" })
	if got := r.cp.Calls("POST", routeRequest)[1].Body["name"]; got != "from-meteorenv" {
		t.Errorf("name %v", got)
	}
}

func TestClaimWaits(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1"}`)
	r.cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "pending"}))
	r.cp.On("GET", routeMQTT, fakecp.Fail(404, "not_found"))
	r.svc.MinPoll = 50 * time.Millisecond
	r.svc.Reload(context.Background())
	r.run()
	res, err := r.svc.Claim(context.Background(), 0)
	if err != nil || res.Status != PollPending || res.RequestID != "r-1" {
		t.Fatalf("one check: %+v %v", res, err)
	}
	r.cp.On("POST", routePoll, fakecp.OK(approved("d-1", "dev_1", nil)))
	res, err = r.svc.Claim(context.Background(), 5*time.Second)
	if err != nil || res.Status != PollApproved || res.Enrollment.DeviceID != "d-1" {
		t.Fatalf("wait: %+v %v", res, err)
	}
	// Claim after the agent collected the identity reports it.
	if res, err := r.svc.Claim(context.Background(), 0); err != nil || res.Status != PollApproved {
		t.Errorf("after: %+v %v", res, err)
	}
	r2 := newRig(t, true)
	if _, err := r2.svc.Claim(context.Background(), 0); codeOf(err) != CodeNoPending {
		t.Errorf("nothing pending: %v", err)
	}
}

func enroll(t *testing.T, r *rig, token string, mqtt map[string]any) {
	t.Helper()
	r.cp.On("POST", routeRegister, fakecp.Created(registered("d-1", token, mqtt)))
	if _, err := r.svc.Register(context.Background(), "reg", ""); err != nil {
		t.Fatal(err)
	}
}

func TestAuthFailedPausesTheTokenUntilTheServerTakesItAgain(t *testing.T) {
	r := newRig(t, true)
	r.cp.On("GET", routeMQTT, fakecp.Fail(404, "not_found"))
	enroll(t, r, "dev_1", nil)
	r.run()
	r.cp.Wait(t, "GET", routeMQTT, 1, 5*time.Second)

	r.svc.Source.AuthFailed("dev_old") // about an older token: ignored
	if _, err := r.svc.Source.Token(); err != nil {
		t.Fatalf("token refused after a report about another token: %v", err)
	}
	r.cp.On("GET", routeMQTT, fakecp.Fail(401, "invalid_token"), fakecp.OK(map[string]any{"enabled": false}))
	before := r.events.enrolled.Load()
	r.svc.Source.AuthFailed("dev_1")
	if _, err := r.svc.Source.Token(); !errors.Is(err, ErrRejected) {
		t.Fatalf("Token after AuthFailed: %v", err)
	}
	if st := r.svc.Source.Status(); st.State != StateRejected {
		t.Errorf("state %s", st.State)
	}
	// The token is usable a moment before the Enrolled event runs: wait for both.
	eventually(t, "token accepted again and Enrolled sent", func() bool {
		_, err := r.svc.Source.Token()
		return err == nil && r.events.enrolled.Load() >= before+1
	})
	time.Sleep(20 * time.Millisecond)
	if r.events.enrolled.Load() != before+1 {
		t.Errorf("Enrolled events %d, want %d", r.events.enrolled.Load(), before+1)
	}
	for _, c := range r.cp.Calls("GET", routeMQTT) {
		if c.Header.Get("Authorization") != "Bearer dev_1" {
			t.Errorf("auth check sent %q", c.Header.Get("Authorization"))
		}
	}
}

func TestMQTTCredentialsFollowTheServerVersion(t *testing.T) {
	r := newRig(t, true)
	mqtt := map[string]any{"host": "broker", "port": 8883, "username": "u", "password": "p1", "tls": true, "ca_cert": nil}
	r.cp.On("GET", routeMQTT, fakecp.OK(map[string]any{"enabled": true, "config_version": "v2"}))
	r.cp.On("POST", routeCreds, fakecp.OK(map[string]any{"host": "broker", "port": 8883, "username": "u",
		"password": "p2", "tls": true, "ca_cert": nil, "config_version": "v2"}))
	enroll(t, r, "dev_1", mqtt)
	r.run()
	eventually(t, "rotated", func() bool { m, _ := r.svc.Source.MQTT(); return m.Password == "p2" && r.events.rotated.Load() == 1 })
	m, _ := r.svc.Source.MQTT()
	if m.ConfigVersion == nil || *m.ConfigVersion != "v2" || r.events.rotated.Load() != 1 {
		t.Errorf("mqtt %+v, rotated %d", m, r.events.rotated.Load())
	}
	if n := len(r.cp.Calls("POST", routeCreds)); n != 1 {
		t.Errorf("credentials fetched %d times", n)
	}
	// The next check finds the same version and fetches nothing.
	r.svc.mu.Lock()
	r.svc.nextMQTT = time.Now()
	r.svc.mu.Unlock()
	r.svc.poke()
	r.cp.Wait(t, "GET", routeMQTT, 2, 5*time.Second)
	if n := len(r.cp.Calls("POST", routeCreds)); n != 1 {
		t.Errorf("credentials fetched %d times", n)
	}
}

func TestFilesWrittenByOthersArePickedUp(t *testing.T) {
	r := newRig(t, false)
	r.cp.On("GET", routeMQTT, fakecp.Fail(404, "not_found"))
	r.run()
	// `meteorcli config` (root) writes config.json; the watcher reloads it.
	r.writeConfig(r.cp.URL)
	eventually(t, "server loaded", func() bool { return r.svc.API.BaseURL() == r.cp.URL && r.events.config.Load() >= 1 })
	// A tool writes a whole identity (an import); the agent starts using it.
	if err := (&config.Device{DeviceID: "d-5"}).Save(r.p.DeviceFile(), config.Owner{}); err != nil {
		t.Fatal(err)
	}
	r.write(r.p.DeviceTokenFile(), "dev_5\n")
	eventually(t, "identity loaded", func() bool { tok, _ := r.svc.Source.Token(); return tok == "dev_5" })
	eventually(t, "Enrolled event", func() bool { return r.events.enrolled.Load() >= 1 })
	// The files are removed (config --domain to another server): not enrolled any more.
	if err := os.Remove(r.p.DeviceTokenFile()); err != nil {
		t.Fatal(err)
	}
	eventually(t, "identity dropped", func() bool { return !r.svc.Source.Enrolled() })
}

func TestStagingRecovery(t *testing.T) {
	p := config.DefaultPaths(t.TempDir())
	staging := p.StagingDir()
	if err := os.MkdirAll(staging, 0o700); err != nil {
		t.Fatal(err)
	}
	// Unfinished: dropped, nothing moves.
	_ = os.WriteFile(filepath.Join(staging, "device-token"), []byte("dev_half"), 0o600)
	s := NewSource(p)
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(p.DeviceTokenFile()); err == nil || s.Enrolled() {
		t.Error("an unfinished staging directory was used")
	}
	// Finished: moved into place.
	_ = os.MkdirAll(staging, 0o700)
	_ = os.WriteFile(filepath.Join(staging, "device-token"), []byte("dev_full"), 0o600)
	_ = os.WriteFile(filepath.Join(staging, "device.json"), []byte(`{"version":1,"device_id":"d-2"}`), 0o600)
	_ = os.WriteFile(filepath.Join(staging, doneFile), []byte("{}"), 0o600)
	_ = os.WriteFile(p.PendingClaimFile(), []byte(`{"request_id":"r","claim_secret":"c"}`), 0o600)
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	if tok, err := s.Token(); tok != "dev_full" || err != nil || s.DeviceID() != "d-2" {
		t.Errorf("after recovery: %q %v %q", tok, err, s.DeviceID())
	}
	if _, err := os.Stat(p.PendingClaimFile()); err == nil {
		t.Error("pending claim kept")
	}
}

func TestDamagedFilesAreMovedAside(t *testing.T) {
	p := config.DefaultPaths(t.TempDir())
	_ = os.MkdirAll(p.IdentityDir, 0o700)
	_ = os.WriteFile(p.DeviceFile(), []byte("{not json"), 0o600)
	_ = os.WriteFile(p.DeviceTokenFile(), []byte("dev_x"), 0o600)
	_ = os.WriteFile(p.MQTTFile(), []byte("[]"), 0o600)
	s := NewSource(p)
	err := s.Load()
	if err == nil || !errors.Is(err, config.ErrCorrupt) {
		t.Fatalf("Load: %v", err)
	}
	if s.Enrolled() {
		t.Error("enrolled with a damaged device.json")
	}
	matches, _ := filepath.Glob(filepath.Join(p.IdentityDir, "*.corrupt-*"))
	if len(matches) != 2 {
		t.Errorf("quarantined %v", matches)
	}
	// An MQTT file without TLS is unusable (MQTT always uses TLS).
	_ = os.WriteFile(p.MQTTFile(), []byte(`{"host":"b","password":"p","tls":false}`), 0o600)
	_ = s.Load()
	if _, ok := s.MQTT(); ok {
		t.Error("MQTT without TLS accepted")
	}
}

func TestSetHeartbeatPersists(t *testing.T) {
	r := newRig(t, true)
	enroll(t, r, "dev_1", nil)
	if err := r.svc.Source.SetHeartbeat(45); err != nil {
		t.Fatal(err)
	}
	d, err := config.LoadDevice(r.p.DeviceFile())
	if err != nil || d.HeartbeatIntervalSeconds != 45 || r.svc.Source.Heartbeat() != 45*time.Second {
		t.Errorf("device %+v %v", d, err)
	}
}

func TestTest(t *testing.T) {
	r := newRig(t, true)
	r.cp.On("GET", "/api/v1/health", fakecp.OK(map[string]any{"status": "ok"}))
	res, err := r.svc.Test(context.Background())
	if err != nil || res.APIKey != "missing" || res.Server != r.cp.URL {
		t.Fatalf("no key: %+v %v", res, err)
	}
	r.write(r.p.APIKeyFile(), "key_x")
	r.svc.Reload(context.Background())
	r.cp.On("POST", "/api/v1/agent/enroll/check", fakecp.OK(map[string]any{"ok": true, "organization_id": "o",
		"organization_name": "Org", "key_name": "techs", "key_prefix": "key_ab"}), fakecp.Fail(401, "invalid_api_key"))
	if res, err = r.svc.Test(context.Background()); err != nil || res.APIKey != "ok" || res.OrganizationName != "Org" {
		t.Errorf("key ok: %+v %v", res, err)
	}
	if res, err = r.svc.Test(context.Background()); err != nil || res.APIKey != "rejected" {
		t.Errorf("key rejected: %+v %v", res, err)
	}
	r.cp.On("GET", "/api/v1/health", fakecp.Fail(502, "bad_gateway"))
	if _, err := r.svc.Test(context.Background()); codeOf(err) != CodeRetryLater {
		t.Errorf("unreachable: %v", err)
	}
}
