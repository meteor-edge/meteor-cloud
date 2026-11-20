package localrpc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// listen opens a socket in a short temp dir (sun_path is limited to 108 bytes).
func listen(t *testing.T) (string, *net.UnixListener) {
	t.Helper()
	dir, err := os.MkdirTemp("", "rpc")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	path := filepath.Join(dir, "s.sock")
	l, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	return path, l
}

func serve(t *testing.T, s *Server) string {
	t.Helper()
	path, l := listen(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Serve(ctx, l) }()
	t.Cleanup(func() {
		cancel()
		if err := <-done; err != nil {
			t.Errorf("Serve: %v", err)
		}
	})
	return path
}

func TestCallAuthorizeAndErrors(t *testing.T) {
	logs := &syncBuffer{}
	var seen Peer
	s := &Server{
		Log: slog.New(slog.NewTextHandler(logs, nil)),
		Authorize: func(p Peer, method string) error {
			seen = p
			if method == "secret" {
				return errors.New("needs meteorcli-admin")
			}
			return nil
		},
		Handlers: map[string]Handler{
			"echo": {Run: func(ctx context.Context, p Peer, params json.RawMessage) (any, error) {
				var in struct{ Name string }
				if err := json.Unmarshal(params, &in); err != nil {
					return nil, Errorf(CodeBadRequest, "bad params")
				}
				return map[string]any{"hello": in.Name, "uid": p.UID}, nil
			}},
			"secret": {Run: func(context.Context, Peer, json.RawMessage) (any, error) { return "x", nil }},
			"boom":   {Run: func(context.Context, Peer, json.RawMessage) (any, error) { return nil, errors.New("disk full") }},
			"slow": {Timeout: 20 * time.Millisecond, Run: func(ctx context.Context, _ Peer, _ json.RawMessage) (any, error) {
				<-ctx.Done()
				return nil, ctx.Err()
			}},
		},
	}
	path := serve(t, s)
	ctx := context.Background()

	var out struct {
		Hello string
		UID   uint32
	}
	if err := Call(ctx, path, "echo", map[string]string{"name": "pi"}, &out); err != nil {
		t.Fatal(err)
	}
	if out.Hello != "pi" || out.UID != uint32(os.Getuid()) || seen.PID != int32(os.Getpid()) {
		t.Errorf("out %+v, peer %+v", out, seen)
	}

	tests := map[string]string{"secret": CodeDenied, "nope": CodeUnknownMethod, "boom": CodeFailed, "slow": CodeFailed}
	for method, code := range tests {
		if err := Call(ctx, path, method, nil, nil); CodeOf(err) != code {
			t.Errorf("%s: err = %v, want %s", method, err, code)
		}
	}
	if err := Call(ctx, path, "echo", "not an object", nil); CodeOf(err) != CodeBadRequest {
		t.Errorf("bad params: %v", err)
	}
	for _, want := range []string{"method=echo", "uid=", "outcome=permission_denied", "outcome=unknown_method"} {
		if !strings.Contains(logs.String(), want) {
			t.Errorf("audit log lacks %q:\n%s", want, logs.String())
		}
	}
}

func TestBadRequestBody(t *testing.T) {
	s := &Server{Log: slog.New(slog.DiscardHandler), Authorize: func(Peer, string) error { return nil }, MaxRequest: 64}
	path := serve(t, s)
	for _, body := range []string{"not json\n", `{"params":{}}` + "\n", `{"method":"x","params":"` + strings.Repeat("a", 100) + `"}`} {
		c, err := net.Dial("unix", path)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = c.Write([]byte(body))
		var resp Response
		_ = json.NewDecoder(c).Decode(&resp)
		c.Close()
		if resp.OK || resp.Error == nil || resp.Error.Code != CodeBadRequest {
			t.Errorf("%q: %+v", body, resp)
		}
	}
}

func TestIdleServerReturns(t *testing.T) {
	_, l := listen(t)
	s := &Server{Log: slog.New(slog.DiscardHandler), Authorize: func(Peer, string) error { return nil }, Idle: 30 * time.Millisecond}
	done := make(chan error, 1)
	go func() { done <- s.Serve(context.Background(), l) }()
	select {
	case err := <-done:
		if err != nil {
			t.Error(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("idle server did not return")
	}
}

func TestCallUnavailableAndDenied(t *testing.T) {
	dir := t.TempDir()
	if err := Call(context.Background(), filepath.Join(dir, "missing.sock"), "x", nil, nil); CodeOf(err) != CodeUnavailable {
		t.Errorf("missing socket: %v", err)
	}
	if os.Geteuid() == 0 {
		t.Skip("root opens any socket")
	}
	path, l := listen(t)
	defer l.Close()
	if err := os.Chmod(path, 0o000); err != nil {
		t.Fatal(err)
	}
	if err := Call(context.Background(), path, "x", nil, nil); CodeOf(err) != CodeDenied {
		t.Errorf("socket without permission: %v", err)
	}
}

func TestSystemGroups(t *testing.T) {
	g := SystemGroups{}
	if g.Member(uint32(os.Getuid()), uint32(os.Getgid()), "no-such-group-meteorcli") {
		t.Error("member of a group that does not exist")
	}
	if _, ok := LookupUID("no-such-user-meteorcli"); ok {
		t.Error("found a missing user")
	}
	if _, ok := LookupGID("no-such-group-meteorcli"); ok {
		t.Error("found a missing group")
	}
	if uid, ok := LookupUID("root"); !ok || uid != 0 {
		t.Errorf("root uid %d %v", uid, ok)
	}
	if !g.Member(0, 0, "root") {
		t.Error("root is not in its primary group")
	}
}
