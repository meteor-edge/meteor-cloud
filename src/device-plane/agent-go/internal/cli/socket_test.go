package cli

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/localrpc"
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
	// Nothing listens on port 9: the heartbeat fails at once instead of reaching out.
	write(t, filepath.Join(root, "etc", "meteorcli", "config.json"), `{"version":1,"domain":"meteor.example","api_base":"http://127.0.0.1:9"}`)
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "identity", "device.json"), `{"version":1,"device_id":"d-1","name":"edge"}`)
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "identity", "device-token"), "dev_secret")

	var logs bytes.Buffer
	notMe := uint32(os.Getuid()) + 1 // the policy must decide, not the same-uid rule
	e := &Env{Version: "9.9.9", Stdout: &logs, Stderr: &logs, Getenv: func(string) string { return "" }, Groups: groups, AgentUID: &notMe}
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

	sock := filepath.Join(root, "run", "meteorcli", "agent.sock")
	before := socketSince(t, sock)
	if before == "" {
		t.Fatal("could not read the socket module's start time before the restart")
	}
	r := meteorcli(t, root, nil, "module", "restart", "socket")
	if r.code != 0 || !strings.Contains(r.stdout, "Module socket restarted.") {
		t.Errorf("module restart = %+v", r)
	}
	// A new run of the socket module (new Since) starts only after the old one
	// closed its socket; wait for it, or the next request may reach neither.
	deadline := time.Now().Add(5 * time.Second)
	for since := ""; since == "" || since == before; since = socketSince(t, sock) {
		if time.Now().After(deadline) {
			t.Fatal("the restarted socket module never served again")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if r := meteorcli(t, root, nil, "module", "restart", "nope"); r.code != 1 || !strings.Contains(r.stderr, "no module named") {
		t.Errorf("unknown module = %+v", r)
	}
	if r := meteorcli(t, root, nil, "module", "stop", "x"); r.code != 2 {
		t.Errorf("usage = %+v", r)
	}
}

// socketSince is when the agent's socket module started its current run ("" if
// the agent does not answer).
func socketSince(t *testing.T, sock string) string {
	t.Helper()
	var r agent.StatusReply
	if err := localrpc.Call(context.Background(), sock, "status", nil, &r); err != nil {
		return ""
	}
	if h := r.Modules["socket"]; h.Since != nil {
		return fmt.Sprint(*h.Since)
	}
	return ""
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
