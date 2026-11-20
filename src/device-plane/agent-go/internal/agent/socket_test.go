package agent

import (
	"context"
	"os"
	"path/filepath"
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
	m := NewSocketModule(nil, fakeGroups{1000: {GroupAdmin}, 1001: {GroupRead}, 1002: {"docker"}})
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

	var ticks atomic.Int64
	worker := ticker("telemetry", &ticks)
	d := testDeps(nil)
	d.Paths, d.Version = p, "9.9.9"
	d.Config = &config.Config{Domain: "meteor.example"}
	sup := fast(NewSupervisor([]Module{worker}, d))
	me := uint32(os.Getuid())
	sup.Add(NewSocketModule(sup, fakeGroups{me: {GroupAdmin}}))
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

	// Restarting the socket module itself answers first, then serves again.
	if err := localrpc.Call(ctx, p.AgentSocket(), "module.restart", ModuleRestartParams{Name: "socket"}, nil); err != nil {
		t.Fatalf("restart socket: %v", err)
	}
	eventually(t, "socket back", func() bool {
		return localrpc.Call(ctx, p.AgentSocket(), "status", nil, &reply) == nil
	})
}

func TestSocketRefusesOutsideGroups(t *testing.T) {
	p := agentRoot(t)
	d := testDeps(nil)
	d.Paths = p
	sup := fast(NewSupervisor(nil, d))
	sup.Add(NewSocketModule(sup, fakeGroups{}))
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
	if s := status(d); s.State != StateNotConfigured {
		t.Errorf("state %s", s.State)
	}
	d.Config = &config.Config{Domain: "meteor.example"}
	if s := status(d); s.State != StateWaitingForEnrollment || s.HeartbeatSeconds != 60 {
		t.Errorf("state %s", s.State)
	}
	_ = os.WriteFile(filepath.Join(p.IdentityDir, "api-key"), []byte("k"), 0o600)
	if s := status(d); !s.APIKey {
		t.Error("api key not seen")
	}
}
