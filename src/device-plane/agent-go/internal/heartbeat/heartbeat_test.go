package heartbeat

import (
	"context"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/platform"
	"meteorcloud/meteorcli/internal/state"
)

const route = "/api/v1/agent/heartbeat"

func answer(interval int, flags ...string) fakecp.Reply {
	body := map[string]any{
		"device_id": "d-1", "status": "online", "heartbeat_interval_seconds": interval,
		"server_time": "2026-11-20T10:00:00Z", "desired_software": []any{}, "wifi_pending": false, "actions_pending": false,
	}
	for _, f := range flags {
		body[f] = true
	}
	return fakecp.OK(body)
}

// deps builds Deps for an enrolled device (token "dev_1") against cp.
func deps(t *testing.T, cp *fakecp.Server, enrolled bool) agent.Deps {
	t.Helper()
	p := config.DefaultPaths(t.TempDir())
	_ = os.MkdirAll(p.IdentityDir, 0o700)
	if enrolled {
		if err := (&config.Device{DeviceID: "d-1", HeartbeatIntervalSeconds: 60}).Save(p.DeviceFile(), config.Owner{}); err != nil {
			t.Fatal(err)
		}
		_ = os.WriteFile(p.DeviceTokenFile(), []byte("dev_1"), 0o600)
	}
	src := identity.NewSource(p)
	if err := src.Load(); err != nil {
		t.Fatal(err)
	}
	client, err := api.New(api.Options{BaseURL: cp.URL, Version: "1.2.3", HTTPClient: cp.Client()})
	if err != nil {
		t.Fatal(err)
	}
	outbox, err := state.NewOutbox(filepath.Join(p.StateDir, "outbox"), 0)
	if err != nil {
		t.Fatal(err)
	}
	health := agent.NewHealth()
	health.Set("deploy", agent.StateParked, errors.New("boom"))
	return agent.Deps{
		Version: "1.2.3", Paths: p, API: client, Identity: src, Outbox: outbox, Health: health,
		Signals: agent.NewSignals(), Log: agent.NewLogger(io.Discard, nil),
		Host: &platform.Host{
			Root:     t.TempDir(),
			Hostname: func() (string, error) { return "edge", nil },
			Uname:    func() (string, string) { return "6.1", "aarch64" },
			LookPath: func(n string) (string, error) {
				if n == "systemctl" {
					return "/bin/systemctl", nil
				}
				return "", errors.New("no")
			},
			Run:    func(context.Context, string, ...string) bool { return false },
			IPv4:   func(string) []net.IP { return nil },
			Statfs: func(string) (uint64, uint64, uint64, bool) { return 0, 0, 0, false },
		},
	}
}

func start(t *testing.T, m *Module, d agent.Deps) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := m.Run(ctx, d); err != nil {
			t.Errorf("Run: %v", err)
		}
	}()
	t.Cleanup(func() { cancel(); wg.Wait() })
}

func TestHeartbeatBodyAndWorkSignals(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", route, answer(5, "actions_pending", "wifi_pending"))
	d := deps(t, cp, true)
	work := d.Signals.Subscribe(func(s agent.Signal) bool { _, ok := s.(agent.WorkWaiting); return ok })
	defer work.Close()
	report, err := d.Outbox.Put(state.KindSoftwareReport, "app", map[string]any{"workload_key": "app", "sync_status": "in_sync"})
	if err != nil {
		t.Fatal(err)
	}

	start(t, &Module{Actions: []string{"reboot"}, DeployKinds: []string{"docker"}}, d)
	call := cp.Wait(t, "POST", route, 1, 5*time.Second)[0]

	if call.Header.Get("Authorization") != "Bearer dev_1" || call.Header.Get("User-Agent") != "meteorcli/1.2.3" ||
		call.Header.Get(api.ProtocolHeader) != "1" {
		t.Errorf("headers %v", call.Header)
	}
	b := call.Body
	if b["hostname"] != "edge" || b["architecture"] != "aarch64" || b["agent_version"] != "1.2.3" {
		t.Errorf("inventory in body: %v", b)
	}
	caps, _ := b["capabilities"].(map[string]any)
	if caps["systemd"] != false || caps["docker"] != false { // no /run/systemd in the fake root
		t.Errorf("tools %v", caps)
	}
	if a, _ := caps["actions"].([]any); len(a) != 1 || a[0] != "reboot" {
		t.Errorf("actions %v", caps["actions"])
	}
	mods, _ := caps["modules"].(map[string]any)
	deploy, _ := mods["deploy"].(map[string]any)
	if deploy["state"] != "parked" || deploy["last_error"] != "boom" {
		t.Errorf("modules %v", mods)
	}
	reports, _ := b["software_report"].([]any)
	if len(reports) != 1 {
		t.Errorf("software_report %v", b["software_report"])
	}

	// The report is acked once the server accepted the heartbeat.
	deadline := time.Now().Add(5 * time.Second)
	for {
		left, _ := d.Outbox.Pending(state.KindSoftwareReport)
		if len(left) == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("report %s not acked", report.ID)
		}
		time.Sleep(5 * time.Millisecond)
	}
	got := map[string]bool{}
	for len(got) < 3 {
		select {
		case <-work.C():
			for _, s := range work.Take() {
				got[s.(agent.WorkWaiting).Kind] = true
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("work signals %v", got)
		}
	}
	// The server's interval is stored for the next start.
	if dev, _ := config.LoadDevice(d.Paths.DeviceFile()); dev.HeartbeatIntervalSeconds != 5 {
		t.Errorf("interval %d", dev.HeartbeatIntervalSeconds)
	}
}

func TestRefusedTokenGoesToIdentity(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", route, fakecp.Fail(401, "invalid_token"))
	d := deps(t, cp, true)
	start(t, &Module{}, d)
	cp.Wait(t, "POST", route, 1, 5*time.Second)
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := d.Identity.Token(); errors.Is(err, identity.ErrRejected) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("identity never heard of the 401")
		}
		time.Sleep(5 * time.Millisecond)
	}
	time.Sleep(50 * time.Millisecond)
	if n := len(cp.Calls("POST", route)); n != 1 {
		t.Errorf("heartbeats with a refused token: %d", n)
	}
	h, _ := d.Health.Get(name)
	if h.State != string(agent.StateWaiting) {
		t.Errorf("health %s", h.State)
	}
}

func TestFailedHeartbeatIsRetriedAndEnrolledBeatsAtOnce(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", route, fakecp.Fail(503, "busy"), answer(3600))
	d := deps(t, cp, false)
	start(t, &Module{}, d)
	time.Sleep(30 * time.Millisecond)
	if n := len(cp.Calls("POST", route)); n != 0 {
		t.Fatalf("beat before enrollment: %d", n)
	}
	// Enrolled: the heartbeat starts without waiting for its idle timer.
	if err := (&config.Device{DeviceID: "d-1"}).Save(d.Paths.DeviceFile(), config.Owner{}); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(d.Paths.DeviceTokenFile(), []byte("dev_1"), 0o600)
	if err := d.Identity.Load(); err != nil {
		t.Fatal(err)
	}
	began := time.Now()
	d.Signals.Publish(agent.Enrolled{})
	calls := cp.Wait(t, "POST", route, 2, 5*time.Second) // 503, then retried within the 1 s backoff
	if calls[0].At.Sub(began) > time.Second {
		t.Errorf("first heartbeat %s after Enrolled", calls[0].At.Sub(began))
	}
	if gap := calls[1].At.Sub(calls[0].At); gap < 400*time.Millisecond || gap > 1100*time.Millisecond {
		t.Errorf("retry after %s, want 0.5-1 s", gap)
	}
}
