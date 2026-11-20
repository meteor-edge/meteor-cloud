package cli

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/fakecp"
)

const (
	routeHeartbeat = "/api/v1/agent/heartbeat"
	routeRegister  = "/api/v1/agent/register"
	routeRequest   = "/api/v1/agent/enroll/request"
	routePoll      = "/api/v1/agent/enroll/poll"
)

var heartbeatOK = fakecp.OK(map[string]any{
	"device_id": "d-1", "status": "online", "heartbeat_interval_seconds": 60,
	"server_time": "2026-11-20T10:00:00Z", "desired_software": []any{}, "wifi_pending": false, "actions_pending": false,
})

func enrollment(extra map[string]any) map[string]any {
	out := map[string]any{"device_id": "d-1", "device_token": "dev_new", "organization_id": "org-1",
		"name": "edge", "heartbeat_interval_seconds": 60, "mqtt": nil}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

// agentProc is one `meteorcli agent` in a scratch root, run as if by another
// user (the meteorcli service user), so socket calls go through the group policy.
type agentProc struct {
	stop func()
}

func startAgent(t *testing.T, root string) *agentProc {
	t.Helper()
	var logs bytes.Buffer
	notMe := uint32(os.Getuid()) + 1
	e := &Env{Version: "9.9.9", Stdout: &logs, Stderr: &logs, Getenv: func(string) string { return "" },
		Groups: groupsOf{agent.GroupAdmin}, AgentUID: &notMe}
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); run(ctx, e, []string{"--root", root, "agent"}) }()
	sock := filepath.Join(root, "run", "meteorcli", "agent.sock")
	deadline := time.Now().Add(5 * time.Second)
	for {
		if conn, err := os.Stat(sock); err == nil && conn != nil {
			break
		}
		if time.Now().After(deadline) {
			cancel()
			wg.Wait()
			t.Fatal("agent socket never appeared")
		}
		time.Sleep(5 * time.Millisecond)
	}
	var once sync.Once
	p := &agentProc{stop: func() { once.Do(func() { cancel(); wg.Wait(); _ = os.Remove(sock) }) }}
	t.Cleanup(p.stop)
	return p
}

// scratchRoot is a short root (Unix socket paths are limited) configured for cp.
func scratchRoot(t *testing.T, cp *fakecp.Server) string {
	t.Helper()
	root, err := os.MkdirTemp("", "cli")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	write(t, filepath.Join(root, "etc", "meteorcli", "config.json"), `{"version":1,"api_base":"`+cp.URL+`"}`)
	return root
}

// Done criterion: with the service running, `meteorcli register` by a
// meteorcli-admin user (no sudo) makes the device online within 5 s.
func TestRegisterThroughRunningAgentBringsDeviceOnline(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", routeRegister, fakecp.Created(enrollment(nil)))
	cp.On("POST", routeHeartbeat, heartbeatOK)
	root := scratchRoot(t, cp)
	startAgent(t, root)
	time.Sleep(50 * time.Millisecond)
	if n := len(cp.Calls("POST", routeHeartbeat)); n != 0 {
		t.Fatalf("heartbeat before enrollment: %d", n)
	}
	tokenFile := filepath.Join(root, "reg-token")
	write(t, tokenFile, "reg_secret\n")

	began := time.Now()
	res := meteorcli(t, root, nil, "register", "--token-file", tokenFile, "--name", "edge")
	if res.code != 0 || !strings.Contains(res.stdout, "Registered device d-1") {
		t.Fatalf("register = %+v", res)
	}
	hb := cp.Wait(t, "POST", routeHeartbeat, 1, 5*time.Second)[0]
	if took := hb.At.Sub(began); took > 5*time.Second {
		t.Errorf("online after %s", took)
	}
	if hb.Header.Get("Authorization") != "Bearer dev_new" {
		t.Errorf("heartbeat auth %q", hb.Header.Get("Authorization"))
	}
	if body := cp.Calls("POST", routeRegister)[0].Body; body["token"] != "reg_secret" || body["name"] != "edge" {
		t.Errorf("register body %v", body)
	}
	if _, err := os.Stat(tokenFile); err == nil {
		t.Error("token file kept after success")
	}
	if strings.Contains(res.stdout+res.stderr, "dev_new") || strings.Contains(res.stdout+res.stderr, "reg_secret") {
		t.Error("a secret was printed")
	}
	st := meteorcli(t, root, nil, "status")
	if !strings.Contains(st.stdout, "Device ID:      d-1") {
		t.Errorf("status %+v", st)
	}
	// Enrolled: a second register must not be needed, and request-token refuses.
	if r := meteorcli(t, root, nil, "request-token", "--api-key", "key_x"); r.code != 2 {
		t.Errorf("request-token while enrolled = %+v", r)
	}
}

// Done criterion: approving a pending request brings the device online with no
// command on the device, also after a reboot while pending.
func TestApprovalAfterRestartWhilePending(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", routeRequest, fakecp.Created(map[string]any{"request_id": "r-1", "claim_secret": "clm_1",
		"status": "pending", "poll_interval_seconds": 10}))
	cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "pending", "poll_interval_seconds": 10}))
	cp.On("POST", routeHeartbeat, heartbeatOK)
	root := scratchRoot(t, cp)
	a := startAgent(t, root)

	res := meteorcli(t, root, nil, "request-token", "--api-key", "key_secret")
	if res.code != 0 || !strings.Contains(res.stdout, "r-1 submitted") {
		t.Fatalf("request-token = %+v", res)
	}
	if r := meteorcli(t, root, nil, "status"); !strings.Contains(r.stdout, "r-1") {
		t.Errorf("status does not show the pending request: %+v", r)
	}
	if r := meteorcli(t, root, nil, "claim"); r.code != 2 || !strings.Contains(r.stdout, "still waiting") {
		t.Errorf("claim while pending = %+v", r)
	}

	// Reboot while pending; the operator approves meanwhile.
	a.stop()
	cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "approved", "poll_interval_seconds": 10,
		"device_id": "d-1", "device_token": "dev_new", "organization_id": "org-1", "name": "edge",
		"heartbeat_interval_seconds": 60, "mqtt": nil}))
	began := time.Now()
	startAgent(t, root)

	hb := cp.Wait(t, "POST", routeHeartbeat, 1, 5*time.Second)[0]
	if took := hb.At.Sub(began); took > 5*time.Second {
		t.Errorf("online %s after the restart", took)
	}
	if hb.Header.Get("Authorization") != "Bearer dev_new" {
		t.Errorf("heartbeat auth %q", hb.Header.Get("Authorization"))
	}
	apiKey := filepath.Join(root, "var", "lib", "meteorcli", "identity", "api-key")
	if _, err := os.Stat(apiKey); err == nil {
		t.Error("API key kept after enrollment")
	}
	if r := meteorcli(t, root, nil, "claim"); r.code != 0 || !strings.Contains(r.stdout, "d-1") {
		t.Errorf("claim after approval = %+v", r)
	}
}

func TestEnrollmentCommandErrors(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", routeRequest, fakecp.Fail(401, "invalid_api_key"), fakecp.Fail(429, "rate_limited", "Retry-After", "42"))
	root := scratchRoot(t, cp)

	if r := meteorcli(t, root, nil, "register", "--token", "reg"); r.code != 1 || !strings.Contains(r.stderr, "not running") {
		t.Errorf("no agent = %+v", r)
	}
	startAgent(t, root)
	tests := []struct {
		args []string
		env  map[string]string
		code int
		err  string
	}{
		{[]string{"register"}, nil, 2, "token is required"},
		{[]string{"register", "--token-file", filepath.Join(root, "nope")}, nil, 2, "not found"},
		{[]string{"request-token"}, nil, 2, "API key"},
		{[]string{"request-token"}, map[string]string{"METEORCLI_API_KEY": "key_bad"}, exitKeyRejected, ""},
		{[]string{"request-token", "--api-key", "key_x"}, nil, exitRetryLater, "retry-after: 42"},
		{[]string{"claim"}, nil, 2, "no pending"},
	}
	for _, tt := range tests {
		r := meteorcli(t, root, tt.env, tt.args...)
		if r.code != tt.code || !strings.Contains(r.stderr, tt.err) {
			t.Errorf("%v = %+v, want exit %d with %q", tt.args, r, tt.code, tt.err)
		}
	}
}

func TestMeteorenvAppliedBeforeStart(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", routeRequest, fakecp.Created(map[string]any{"request_id": "r-1", "claim_secret": "clm_1", "status": "pending"}))
	cp.On("POST", routePoll, fakecp.OK(map[string]any{"status": "pending"}))
	root, err := os.MkdirTemp("", "cli")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	env := filepath.Join(root, "boot", "firmware", "meteorenv.txt")
	write(t, env, "\ufeff# first boot\nMETEOR_API_BASE=\""+cp.URL+"\"\nMETEOR_API_KEY='key_boot'\nMETEOR_NAME=lobby-cam\nWIFI_SSID=home\n")

	r := meteorcli(t, root, nil, "config", "meteorenv")
	if r.code != 0 || !strings.Contains(r.stdout, "meteorenv.txt") || strings.Contains(r.stdout+r.stderr, "key_boot") {
		t.Fatalf("config meteorenv = %+v", r)
	}
	if _, err := os.Stat(env); err == nil {
		t.Error("meteorenv file kept")
	}
	// The agent starts, finds the saved key and asks for approval by itself.
	startAgent(t, root)
	call := cp.Wait(t, "POST", routeRequest, 1, 5*time.Second)[0]
	if call.Header.Get("Authorization") != "Bearer key_boot" || call.Body["name"] != "lobby-cam" {
		t.Errorf("request %v %v", call.Header, call.Body)
	}
	// An enrolled device ignores a new file.
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "identity", "device-token"), "dev_x")
	write(t, env, "METEOR_API_BASE=https://other.example\n")
	if r := meteorcli(t, root, nil, "config", "meteorenv"); r.code != 0 || !strings.Contains(r.stderr, "already enrolled") {
		t.Errorf("enrolled = %+v", r)
	}
	if _, err := os.Stat(env); err != nil {
		t.Error("file removed on an enrolled device")
	}
	// Nothing to do without a file.
	_ = os.Remove(env)
	if r := meteorcli(t, root, nil, "config", "meteorenv"); r.code != 0 || r.stdout != "" {
		t.Errorf("no file = %+v", r)
	}
}

func TestRegisterRefusesTokenAndTokenFile(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "tok")
	write(t, file, "reg_file")
	r := meteorcli(t, root, nil, "register", "--token", "reg_flag", "--token-file", file)
	if r.code != 2 || !strings.Contains(r.stderr, "not both") {
		t.Errorf("both flags = %+v", r)
	}
	if _, err := os.Stat(file); err != nil {
		t.Error("the token file was touched")
	}
}
