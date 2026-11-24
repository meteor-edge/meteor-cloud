package identity

// Regression tests for races and edge cases around server changes, staging,
// polling schedules and Claim.

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/fakecp"
)

// An approval collected from the old server after `meteorcli config` switched
// to another one must not be stored: the old server's token would go to the new one.
func TestApprovalFromAReplacedServerIsDiscarded(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1"}`)
	r.svc.Reload(context.Background())
	other := fakecp.New(t)
	// The poll is answered after the CLI switched servers (DropEnrollment + Save).
	r.cp.On("POST", routePoll, fakecp.Reply{Status: 200, Body: approved("d-1", "dev_old_server", nil)})
	r.svc.mu.Lock()
	if _, err := config.DropEnrollment(r.p); err != nil {
		t.Fatal(err)
	}
	r.writeConfig(other.URL)
	got := r.svc.pollOnce(context.Background())
	r.svc.mu.Unlock()
	if got == PollApproved || r.svc.Source.Enrolled() {
		t.Fatalf("poll = %s, enrolled %v: an approval from the replaced server was stored", got, r.svc.Source.Enrolled())
	}
	if _, err := os.Stat(r.p.DeviceTokenFile()); err == nil {
		t.Error("the old server's token was written")
	}
	if r.svc.approved != nil {
		t.Error("the stale approval is kept for a retry")
	}
}

// A server whose settings cannot be used gets no requests, and none go to
// the previous server either; status and commands say why.
func TestUnusableServerSendsNothing(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.APIKeyFile(), "key_x")
	other := fakecp.New(t)
	if err := (&config.Config{APIBase: "https://" + other.Listener.Addr().String(), CAFile: "/nonexistent/ca.pem"}).
		Save(r.p.ConfigFile, config.Owner{}); err != nil {
		t.Fatal(err)
	}
	// A client that builds its own TLS settings (the rig's fixed test client skips them).
	client, err := api.New(api.Options{Version: "1.2.3"})
	if err != nil {
		t.Fatal(err)
	}
	r.svc.API = client
	r.events.config.Store(0)
	r.svc.Reload(context.Background())
	if _, err := r.svc.Test(context.Background()); codeOf(err) != CodeNotConfigured {
		t.Errorf("Test: %v", err)
	}
	if _, err := r.svc.RequestToken(context.Background(), RequestOptions{}); codeOf(err) != CodeNotConfigured {
		t.Errorf("RequestToken: %v", err)
	}
	if _, err := r.svc.Register(context.Background(), "reg", ""); codeOf(err) != CodeNotConfigured {
		t.Errorf("Register: %v", err)
	}
	if r.svc.API.BaseURL() != "" {
		t.Errorf("the client still talks to %q", r.svc.API.BaseURL())
	}
	if n := len(r.cp.Calls("POST", routeRequest)) + len(r.cp.Calls("POST", routeRegister)); n != 0 {
		t.Errorf("%d requests went to the previous server", n)
	}
	if r.events.config.Load() != 0 {
		t.Error("ConfigChanged announced for settings that were not applied")
	}
	// Fixed: the next reload applies it.
	r.writeConfig(other.URL)
	r.svc.Reload(context.Background())
	if r.svc.API.BaseURL() != other.URL || r.events.config.Load() != 1 {
		t.Errorf("after the fix: %q, ConfigChanged %d", r.svc.API.BaseURL(), r.events.config.Load())
	}
}

// A finished stage of an earlier commit is moved into place before a new
// commit, and a stage that cannot be finished keeps the identity in memory.
func TestStagingIsFinishedBeforeTheNextCommit(t *testing.T) {
	r := newRig(t, true)
	enroll(t, r, "dev_1", nil)
	staging := r.p.StagingDir()
	_ = os.MkdirAll(staging, 0o700)
	_ = os.WriteFile(filepath.Join(staging, "device.json"), []byte(`{"version":1,"device_id":"d-staged"}`), 0o600)
	_ = os.WriteFile(filepath.Join(staging, doneFile), []byte("{}"), 0o600)
	if err := r.svc.Source.files.commit(registeredEnrollment("d-2", "dev_2")); err != nil {
		t.Fatal(err)
	}
	if d, _ := config.LoadDevice(r.p.DeviceFile()); d.DeviceID != "d-2" {
		t.Errorf("device %q", d.DeviceID)
	}
	if err := r.svc.Source.Load(); err != nil {
		t.Fatal(err)
	}

	// A stage that cannot be moved (a directory in the way): keep the old identity.
	_ = os.MkdirAll(staging, 0o700)
	_ = os.WriteFile(filepath.Join(staging, "device-token"), []byte("dev_3"), 0o600)
	_ = os.WriteFile(filepath.Join(staging, doneFile), []byte("{}"), 0o600)
	_ = os.MkdirAll(filepath.Join(staging, "device.json", "x"), 0o700)
	err := r.svc.Source.Load()
	if err == nil {
		t.Fatal("Load hid the failed recovery")
	}
	if tok, _ := r.svc.Source.Token(); tok != "dev_2" {
		t.Errorf("token in memory = %q, want the last good one", tok)
	}
}

func TestTestReportsServerTroubleNotKeyRejection(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.APIKeyFile(), "key_x")
	r.svc.Reload(context.Background())
	r.cp.On("GET", "/api/v1/health", fakecp.OK(map[string]any{"status": "ok"}))
	r.cp.On("POST", "/api/v1/agent/enroll/check", fakecp.Fail(503, "busy"))
	res, err := r.svc.Test(context.Background())
	if err == nil || res.APIKey == "rejected" {
		t.Errorf("503 = %+v, %v; want an error, not a rejected key", res, err)
	}
}

func TestRequestTokenOnEnrolledDeviceKeepsTheSavedKey(t *testing.T) {
	r := newRig(t, true)
	enroll(t, r, "dev_1", nil)
	r.write(r.p.APIKeyFile(), "key_old")
	r.svc.Reload(context.Background())
	if _, err := r.svc.RequestToken(context.Background(), RequestOptions{APIKey: "key_new"}); codeOf(err) != CodeAlreadyEnrolled {
		t.Fatalf("err %v", err)
	}
	if data, _ := os.ReadFile(r.p.APIKeyFile()); string(data) != "key_old" {
		t.Errorf("api key = %q", data)
	}
}

// A key refused for the automatic request is not sent again after a restart.
func TestRefusedAutoRequestIsRemembered(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.APIKeyFile(), "key_bad")
	r.cp.On("POST", routeRequest, fakecp.Fail(401, "invalid_api_key"))
	r.svc.Reload(context.Background())
	r.run()
	r.cp.Wait(t, "POST", routeRequest, 1, 5*time.Second)
	eventually(t, "refusal stored", func() bool { _, err := os.Stat(r.p.AutoRequestFile()); return err == nil })
	data, _ := os.ReadFile(r.p.AutoRequestFile())
	if string(data) == "" || strings.Contains(string(data), "key_bad") {
		t.Errorf("auto-request.json %q must hold a hash, not the key", data)
	}
	// "Restart": a new service over the same files sends nothing.
	svc2 := &Service{Source: NewSource(r.p), API: r.svc.API, Log: r.svc.Log, MinPoll: 20 * time.Millisecond, Tick: 50 * time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	_ = svc2.Run(ctx)
	if n := len(r.cp.Calls("POST", routeRequest)); n != 1 {
		t.Errorf("requests after restart = %d, want 1", n)
	}
}

func TestResubmitEndsOnNotFoundAndHonoursRetryAfter(t *testing.T) {
	for _, tt := range []struct {
		reply   fakecp.Reply
		dropped bool
		wait    time.Duration
	}{
		{fakecp.Fail(404, "not_found"), true, 0},
		{fakecp.Fail(429, "rate_limited", "Retry-After", "120"), false, 120 * time.Second},
	} {
		r := newRig(t, true)
		r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1"}`)
		r.write(r.p.APIKeyFile(), "key_x")
		r.svc.Reload(context.Background())
		r.cp.On("POST", routeRequest, tt.reply)
		r.svc.mu.Lock()
		r.svc.resubmit(context.Background())
		next := time.Until(r.svc.nextPoll)
		r.svc.mu.Unlock()
		if dropped := r.svc.Source.Status().PendingRequestID == ""; dropped != tt.dropped {
			t.Errorf("%d: dropped = %v", tt.reply.Status, dropped)
		}
		if tt.wait > 0 && next < tt.wait-time.Second {
			t.Errorf("%d: next try in %s, want %s", tt.reply.Status, next, tt.wait)
		}
	}
}

// Claim returns at its deadline (plus a bounded grace for the first answer)
// even when no poll answers, and never polls before the server's Retry-After.
func TestClaimIsBoundedAndHonoursBackoff(t *testing.T) {
	r := newRig(t, true)
	r.write(r.p.PendingClaimFile(), `{"request_id":"r-1","claim_secret":"clm_1"}`)
	r.svc.Reload(context.Background())
	r.svc.mu.Lock()
	r.svc.backoffUntil = time.Now().Add(time.Hour)
	r.svc.nextPoll = r.svc.backoffUntil
	r.svc.mu.Unlock()
	r.svc.ClaimGrace = 100 * time.Millisecond
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	began := time.Now()
	res, err := r.svc.Claim(ctx, 10*time.Millisecond)
	if err != nil || res.Status != PollPending {
		t.Fatalf("claim: %+v %v", res, err)
	}
	if took := time.Since(began); took > 2*time.Second {
		t.Errorf("claim took %s", took)
	}
	r.svc.mu.Lock()
	defer r.svc.mu.Unlock()
	if r.svc.nextPoll.Before(r.svc.backoffUntil) {
		t.Error("claim moved the poll before the server's Retry-After")
	}
}

func TestChecksOfReplacedRequestsAreIgnored(t *testing.T) {
	s := NewSource(config.DefaultPaths(t.TempDir()))
	s.pending = &PendingClaim{RequestID: "r-2"}
	s.markChecked("r-1", time.Now())
	if !s.Status().PendingChecked.IsZero() {
		t.Error("a check of r-1 counted for r-2")
	}
}

func TestHeartbeatIntervalStaysUnsavedOnFailure(t *testing.T) {
	r := newRig(t, true)
	enroll(t, r, "dev_1", nil)
	if err := os.Chmod(r.p.IdentityDir, 0o500); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(r.p.IdentityDir, 0o700)
	if os.Getuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	if err := r.svc.Source.SetHeartbeat(45); err == nil {
		t.Fatal("save succeeded in a read-only directory")
	}
	if r.svc.Source.Heartbeat() == 45*time.Second {
		t.Error("the unsaved interval is in use")
	}
	_ = os.Chmod(r.p.IdentityDir, 0o700)
	if err := r.svc.Source.SetHeartbeat(45); err != nil {
		t.Fatal(err)
	}
	if d, _ := config.LoadDevice(r.p.DeviceFile()); d.HeartbeatIntervalSeconds != 45 {
		t.Errorf("saved %d", d.HeartbeatIntervalSeconds)
	}
}

// MQTT turned back on at a new version stays off until the new credentials are stored.
func TestMQTTStaysOffUntilNewCredentialsAreStored(t *testing.T) {
	r := newRig(t, true)
	mqtt := map[string]any{"host": "broker", "port": 8883, "username": "u", "password": "p1", "tls": true, "ca_cert": nil}
	enroll(t, r, "dev_1", mqtt)
	r.svc.Source.setMQTTOff(true)
	r.cp.On("GET", routeMQTT, fakecp.OK(map[string]any{"enabled": true, "config_version": "v2"}))
	r.cp.On("POST", routeCreds, fakecp.Fail(503, "busy"))
	r.svc.syncMQTT(context.Background(), r.svc.Source.snapshot())
	if _, ok := r.svc.Source.MQTT(); ok {
		t.Error("old credentials handed out after a failed fetch")
	}
}

func registeredEnrollment(id, token string) api.Enrollment {
	return api.Enrollment{DeviceID: id, DeviceToken: token, OrganizationID: "org-1", Name: "edge", HeartbeatIntervalSeconds: 30}
}
