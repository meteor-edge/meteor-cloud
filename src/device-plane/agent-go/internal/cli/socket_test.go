package cli

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
)

// groupsOf puts the test user in the given groups for the agent socket.
type groupsOf []string

func (g groupsOf) Member(uid, _ uint32, group string) bool {
	if uid != uint32(os.Getuid()) {
		return false
	}
	for _, have := range g {
		if have == group {
			return true
		}
	}
	return false
}

// runningAgent starts `meteorcli agent` in a short scratch root (Unix socket
// paths are limited to 108 bytes) with the test user in groups.
func runningAgent(t *testing.T, groups groupsOf) string {
	t.Helper()
	root, err := os.MkdirTemp("", "cli")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	write(t, filepath.Join(root, "etc", "meteorcli", "config.json"), `{"version":1,"domain":"meteor.example"}`)
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "identity", "device.json"), `{"version":1,"device_id":"d-1","name":"edge"}`)
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "identity", "device-token"), "dev_secret")

	var logs bytes.Buffer
	e := &Env{Version: "9.9.9", Stdout: &logs, Stderr: &logs, Getenv: func(string) string { return "" }, Groups: groups}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan int, 1)
	go func() { done <- run(ctx, e, []string{"--root", root, "agent"}) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("agent did not stop")
		}
	})
	sock := filepath.Join(root, "run", "meteorcli", "agent.sock")
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(sock); err == nil {
			return root
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("agent socket never appeared")
	return ""
}

func TestStatusAndModuleRestartThroughAgent(t *testing.T) {
	root := runningAgent(t, groupsOf{agent.GroupAdmin})

	st := meteorcli(t, root, nil, "status")
	if st.code != 0 {
		t.Fatalf("status = %+v", st)
	}
	for _, want := range []string{"Agent:          running 9.9.9 (enrolled)", "Device ID:      d-1", "Credential:     present", "Module socket:"} {
		if !strings.Contains(st.stdout, want) {
			t.Errorf("status lacks %q:\n%s", want, st.stdout)
		}
	}
	if strings.Contains(st.stdout, "dev_secret") {
		t.Error("status printed the token")
	}

	r := meteorcli(t, root, nil, "module", "restart", "socket")
	if r.code != 0 || !strings.Contains(r.stdout, "Module socket restarted.") {
		t.Errorf("module restart = %+v", r)
	}
	// The socket module listens again a moment after its restart.
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(meteorcli(t, root, nil, "status").stdout, "Agent:          running") && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if r := meteorcli(t, root, nil, "module", "restart", "nope"); r.code != 1 || !strings.Contains(r.stderr, "no module named") {
		t.Errorf("unknown module = %+v", r)
	}
	if r := meteorcli(t, root, nil, "module", "stop", "x"); r.code != 2 {
		t.Errorf("usage = %+v", r)
	}
}

func TestReadGroupCannotRestartModules(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root is always allowed")
	}
	root := runningAgent(t, groupsOf{agent.GroupRead})
	if st := meteorcli(t, root, nil, "status"); st.code != 0 {
		t.Errorf("status = %+v", st)
	}
	r := meteorcli(t, root, nil, "module", "restart", "socket")
	if r.code != 1 || !strings.Contains(r.stderr, "needs the meteorcli-admin group") {
		t.Errorf("module restart = %+v", r)
	}
}

func TestOutsiderIsRefused(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root is always allowed")
	}
	root := runningAgent(t, groupsOf{"docker"})
	r := meteorcli(t, root, nil, "status")
	if r.code != 1 || !strings.Contains(r.stderr, "permission_denied") || strings.Contains(r.stdout, "d-1") {
		t.Errorf("status = %+v", r)
	}
}

func TestModuleAndHelperCallWithoutServers(t *testing.T) {
	root := t.TempDir()
	if r := meteorcli(t, root, nil, "module", "restart", "x"); r.code != 1 || !strings.Contains(r.stderr, "not running") {
		t.Errorf("module = %+v", r)
	}
	if r := meteorcli(t, root, nil, "helper", "call", "power.reboot"); r.code != 1 || !strings.Contains(r.stderr, "unavailable") {
		t.Errorf("helper call = %+v", r)
	}
	if r := meteorcli(t, root, nil, "helper", "call", "logs.read", "{bad"); r.code != 2 {
		t.Errorf("bad json = %+v", r)
	}
	if r := meteorcli(t, root, nil, "helper", "call"); r.code != 2 {
		t.Errorf("usage = %+v", r)
	}
	if os.Geteuid() != 0 {
		if r := meteorcli(t, root, nil, "helper"); r.code != 1 || !strings.Contains(r.stderr, "must run as root") {
			t.Errorf("helper as non-root = %+v", r)
		}
	}
}
