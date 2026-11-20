package agent

import (
	"context"
	"fmt"
	"meteorcloud/meteorcli/internal/identity"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// fakeGroups says which groups each uid is in.
type fakeGroups map[uint32][]string

func (f fakeGroups) Member(uid, _ uint32, group string) bool {
	for _, g := range f[uid] {
		if g == group {
			return true
		}
	}
	return false
}

func TestSocketPolicy(t *testing.T) {
	m := NewSocketModule(nil, fakeGroups{1000: {GroupAdmin}, 1001: {GroupRead}, 1002: {"docker"}}, nil)
	// Not the test process's uid: the agent's own user is always allowed, and CI
	// runs as uid 1001, which this table uses for a read-only member.
	m.OwnUID = 4242
	tests := []struct {
		uid    uint32
		method string
		code   string
	}{
		{0, "module.restart", ""},
		{1000, "status", ""},
		{1000, "module.restart", ""},
		{1001, "status", ""},
		{1001, "module.restart", localrpc.CodeDenied},
		{1002, "status", localrpc.CodeDenied},
		{1002, "module.restart", localrpc.CodeDenied},
		{0, "shell", localrpc.CodeUnknownMethod},
	}
	for _, tt := range tests {
		err := m.authorize(localrpc.Peer{UID: tt.uid, GID: tt.uid}, tt.method)
		if localrpc.CodeOf(err) != tt.code || (tt.code == "" && err != nil) {
			t.Errorf("uid %d %s: err = %v, want %q", tt.uid, tt.method, err, tt.code)
		}
	}
}

// agentRoot is a short scratch root: Unix socket paths are limited to 108 bytes.
func agentRoot(t *testing.T) config.Paths {
	t.Helper()
	dir, err := os.MkdirTemp("", "agt")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	p := config.DefaultPaths(dir)
	for _, d := range []string{p.RuntimeDir, p.IdentityDir} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	return p
}

func TestSocketStatusAndModuleRestart(t *testing.T) {
	p := agentRoot(t)
	if err := os.WriteFile(p.DeviceTokenFile(), []byte("dev_x"), 0o600); err != nil {
		t.Fatal(err)
	}
	dev := &config.Device{DeviceID: "d-1", OrganizationID: "o-1", Name: "edge", HeartbeatIntervalSeconds: 30}
	if err := dev.Save(p.DeviceFile(), config.Owner{}); err != nil {
		t.Fatal(err)
	}

	if err := (&config.Config{Domain: "meteor.example"}).Save(p.ConfigFile, config.Owner{}); err != nil {
		t.Fatal(err)
	}

	var ticks atomic.Int64
	worker := ticker("telemetry", &ticks)
	d := testDeps(nil)
	d.Paths, d.Version = p, "9.9.9"
	d.Identity = identity.NewSource(p)
	if err := d.Identity.Load(); err != nil {
		t.Fatal(err)
	}
	sup := fast(NewSupervisor([]Module{worker}, d))
	me := uint32(os.Getuid())
	sup.Add(NewSocketModule(sup, fakeGroups{me: {GroupAdmin}}, nil))
	stop := start(t, sup)
	defer stop()

	ctx := context.Background()
	var reply StatusReply
	eventually(t, "socket answers", func() bool {
		return localrpc.Call(ctx, p.AgentSocket(), "status", nil, &reply) == nil
	})
	if reply.State != StateEnrolled || reply.DeviceID != "d-1" || reply.Server != "https://meteor.example" ||
		!reply.Credential || reply.APIKey || reply.HeartbeatSeconds != 30 || reply.Version != "9.9.9" {
		t.Errorf("status %+v", reply)
	}
	if _, ok := reply.Modules["socket"]; !ok {
		t.Errorf("modules %v", reply.Modules)
	}
	if st, _ := os.Stat(p.AgentSocket()); st.Mode().Perm() != 0o660 {
		t.Errorf("socket mode %v", st.Mode().Perm())
	}

	if err := localrpc.Call(ctx, p.AgentSocket(), "module.restart", ModuleRestartParams{Name: "telemetry"}, nil); err != nil {
		t.Fatal(err)
	}
	eventually(t, "telemetry restarted", func() bool { return worker.runs.Load() == 2 })
	if err := localrpc.Call(ctx, p.AgentSocket(), "module.restart", ModuleRestartParams{Name: "nope"}, nil); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("unknown module: %v", err)
	}
	if err := localrpc.Call(ctx, p.AgentSocket(), "module.restart", nil, nil); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("no params: %v", err)
	}

	// Restarting the socket module itself answers first, then a new run serves.
	// Each run sets a new Since, and a run starts only after the previous one
	// closed its socket, so a new Since means the old server is gone.
	before := sinceOf(reply)
	if err := localrpc.Call(ctx, p.AgentSocket(), "module.restart", ModuleRestartParams{Name: "socket"}, nil); err != nil {
		t.Fatalf("restart socket: %v", err)
	}
	eventually(t, "new socket serves", func() bool {
		var r StatusReply
		return localrpc.Call(ctx, p.AgentSocket(), "status", nil, &r) == nil && sinceOf(r) != before
	})
}

// sinceOf is when the socket module's current run started.
func sinceOf(r StatusReply) string {
	if h := r.Modules["socket"]; h.Since != nil {
		return fmt.Sprint(*h.Since)
	}
	return ""
}

func TestAgentSocketOwnership(t *testing.T) {
	p := agentRoot(t)
	path := p.AgentSocket()

	// A socket left by a dead agent (nobody holds the lock) is replaced.
	stale, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	stale.SetUnlinkOnClose(false)
	stale.Close()

	// Agents starting at the same time: exactly one gets the socket.
	type result struct {
		l       *net.UnixListener
		release func()
		err     error
	}
	results := make(chan result, 8)
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			l, release, err := listenAgentSocket(path)
			results <- result{l, release, err}
		}()
	}
	wg.Wait()
	close(results)
	var owner result
	won := 0
	for r := range results {
		if r.err == nil {
			won++
			owner = r
		} else if !strings.Contains(r.err.Error(), "another agent owns") {
			t.Errorf("loser error: %v", r.err)
		}
	}
	if won != 1 {
		t.Fatalf("%d agents got the socket", won)
	}
	if c, err := net.Dial("unix", path); err != nil {
		t.Errorf("owner's socket is gone: %v", err)
	} else {
		c.Close()
	}

	// After the owner closes, the next agent takes over.
	owner.l.Close()
	owner.release()
	l, release, err := listenAgentSocket(path)
	if err != nil {
		t.Fatalf("after release: %v", err)
	}
	l.Close()
	release()

	// A live socket from a process that does not take the lock is kept.
	live, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := listenAgentSocket(path); err == nil || !strings.Contains(err.Error(), "another process serves") {
		t.Errorf("live socket without lock: %v", err)
	}
	if c, err := net.Dial("unix", path); err != nil {
		t.Errorf("live socket was removed: %v", err)
	} else {
		c.Close()
	}
	live.Close()

	// Anything that is not a socket is refused and kept.
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := listenAgentSocket(path); err == nil || !exists(path) {
		t.Errorf("regular file: %v", err)
	}
}

func TestSocketRefusesOutsideGroups(t *testing.T) {
	p := agentRoot(t)
	d := testDeps(nil)
	d.Paths = p
	sup := fast(NewSupervisor(nil, d))
	sock := NewSocketModule(sup, fakeGroups{}, nil)
	sock.OwnUID = uint32(os.Getuid()) + 1 // the group policy decides, not the same-uid rule
	sup.Add(sock)
	stop := start(t, sup)
	defer stop()

	deadline := time.Now().Add(5 * time.Second)
	var err error
	for time.Now().Before(deadline) {
		err = localrpc.Call(context.Background(), p.AgentSocket(), "status", nil, nil)
		if localrpc.CodeOf(err) != localrpc.CodeUnavailable {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if os.Getuid() == 0 {
		if err != nil {
			t.Errorf("root refused: %v", err)
		}
		return
	}
	if localrpc.CodeOf(err) != localrpc.CodeDenied {
		t.Errorf("err = %v, want permission_denied", err)
	}
}

func TestStatusStates(t *testing.T) {
	p := agentRoot(t)
	d := testDeps(nil)
	d.Paths = p
	d.Identity = identity.NewSource(p)
	load := func() {
		t.Helper()
		if err := d.Identity.Load(); err != nil {
			t.Fatal(err)
		}
	}
	load()
	if s := status(d); s.State != StateNotConfigured {
		t.Errorf("state %s", s.State)
	}
	if err := (&config.Config{Domain: "meteor.example"}).Save(p.ConfigFile, config.Owner{}); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(p.IdentityDir, "api-key"), []byte("k"), 0o600)
	load()
	if s := status(d); s.State != StateWaitingForEnrollment || s.HeartbeatSeconds != 60 || !s.APIKey {
		t.Errorf("status %+v", s)
	}
	_ = os.WriteFile(p.PendingClaimFile(), []byte(`{"request_id":"r-1","claim_secret":"clm_x"}`), 0o600)
	load()
	if s := status(d); s.State != StatePendingApproval || s.PendingRequestID != "r-1" || !s.PendingClaim {
		t.Errorf("status %+v", s)
	}
}
