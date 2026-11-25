package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"

	"meteorcloud/meteorcli/internal/localrpc"
)

// fakeHost records tool runs and answers with canned output.
type fakeHost struct {
	mu     sync.Mutex
	calls  [][]string
	output string
	fail   string // tool name that fails
}

func (f *fakeHost) host(unitDir string) Host {
	run := func(_ context.Context, name string, args ...string) ([]byte, error) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.calls = append(f.calls, append([]string{name}, args...))
		if name == f.fail || (len(args) > 0 && args[0] == f.fail) {
			return nil, errors.New(name + " failed")
		}
		return []byte(f.output), nil
	}
	stream := func(ctx context.Context, w io.Writer, name string, args ...string) error {
		out, err := run(ctx, name, args...)
		if err != nil {
			return err
		}
		_, err = w.Write(out)
		return err
	}
	return Host{UnitDir: unitDir, Run: run, Stream: stream}
}

func call(t *testing.T, h Host, op string, params any) (json.RawMessage, error) {
	t.Helper()
	raw, _ := json.Marshal(params)
	if params == nil {
		raw = nil
	}
	out, err := Ops(h)[op].Run(context.Background(), localrpc.Peer{}, raw)
	if err != nil {
		return nil, err
	}
	b, _ := json.Marshal(out)
	return b, nil
}

func TestPower(t *testing.T) {
	f := &fakeHost{}
	h := f.host(t.TempDir())
	for op, verb := range map[string]string{"power.reboot": "reboot", "power.shutdown": "poweroff"} {
		out, err := call(t, h, op, nil)
		if err != nil || string(out) != `{"started":true}` {
			t.Errorf("%s = %s, %v", op, out, err)
		}
		last := f.calls[len(f.calls)-1]
		if !slices.Equal(last, []string{"systemctl", "--no-block", verb}) {
			t.Errorf("%s ran %v", op, last)
		}
	}
	if _, err := call(t, h, "power.reboot", map[string]any{"delay": 5}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("unknown param: %v", err)
	}
	f.fail = "systemctl"
	if _, err := call(t, h, "power.shutdown", nil); err == nil {
		t.Error("failing systemctl: want error")
	}
}

func TestLogsRead(t *testing.T) {
	f := &fakeHost{output: "a ERROR one\nb ok\nc error two\nd error three\n"}
	h := f.host(t.TempDir())

	out, err := call(t, h, "logs.read", LogsRequest{Source: "systemd", Unit: "nginx.service", Lines: 2, Since: "15m", Grep: "Error"})
	if err != nil {
		t.Fatal(err)
	}
	var reply LogsReply
	_ = json.Unmarshal(out, &reply)
	if !slices.Equal(reply.Lines, []string{"c error two", "d error three"}) {
		t.Errorf("lines %v", reply.Lines)
	}
	want := []string{"journalctl", "--no-pager", "--quiet", "--output=short-iso", "--lines=50000", "--unit=nginx.service", "--since=-15min"}
	if !slices.Equal(f.calls[0], want) {
		t.Errorf("ran %v", f.calls[0])
	}

	out, _ = call(t, h, "logs.read", LogsRequest{Source: "journal"})
	_ = json.Unmarshal(out, &reply)
	if len(reply.Lines) != 4 || !slices.Contains(f.calls[1], "--lines=200") {
		t.Errorf("journal: %v ran %v", reply.Lines, f.calls[1])
	}

	bad := []LogsRequest{
		{Source: "docker", Unit: "x"},
		{Source: "file"},
		{Source: "journal", Unit: "x.service"},
		{Source: "systemd", Unit: "-x.service"},
		{Source: "systemd", Unit: "Nginx.service"},
		{Source: "systemd", Unit: "nginx"},
		{Source: "journal", Lines: 10001},
		{Source: "journal", Lines: -1},
		{Source: "journal", Since: "1y"},
		{Source: "journal", Since: "0m"},
		{Source: "journal", Grep: "a\nb"},
		{Source: "journal", Grep: strings.Repeat("x", 201)},
	}
	for _, r := range bad {
		if _, err := call(t, h, "logs.read", r); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%+v: err = %v, want bad_request", r, err)
		}
	}
}

func TestMatcher(t *testing.T) {
	var b strings.Builder
	for i := range 1000 {
		b.WriteString("line ")
		b.WriteString(strings.Repeat("x", i%3))
		b.WriteString("\n")
	}
	m := newMatcher("", 5)
	_, _ = m.Write([]byte(b.String()))
	if got := m.lines(); len(got) != 5 {
		t.Errorf("got %d", len(got))
	}
	if got := newMatcher("x", 5).lines(); got == nil || len(got) != 0 {
		t.Errorf("empty output: %#v", got)
	}

	// A long line is searched in full (the match is past the kept part and split
	// across writes), kept cut with a note, and the lines after it are searched too.
	m = newMatcher("ERROR", 10)
	big := strings.Repeat("a", 3*maxLineBytes) + " Err"
	for _, chunk := range []string{big[:1000], big[1000:], "or at the end\nno match\nshort error li", "ne\nok\n", "tail ERROR"} {
		_, _ = m.Write([]byte(chunk))
	}
	got := m.lines()
	if len(got) != 3 {
		t.Fatalf("lines %d: %.80q", len(got), got)
	}
	wantDropped := 3*maxLineBytes + len(" Error at the end") - maxLineBytes
	if !strings.HasSuffix(got[0], fmt.Sprintf(" …[truncated %d bytes]", wantDropped)) || len(got[0]) > maxLineBytes+40 {
		t.Errorf("long line %d bytes, ends %q", len(got[0]), got[0][len(got[0])-40:])
	}
	if got[1] != "short error line" || got[2] != "tail ERROR" {
		t.Errorf("lines %q", got[1:])
	}

	// A long line that does not match is not kept; non-ASCII text is untouched.
	m = newMatcher("zzz", 5)
	_, _ = m.Write([]byte(strings.Repeat("y", 2*maxLineBytes) + "\nÄrger zzz\n"))
	if got := m.lines(); len(got) != 1 || got[0] != "Ärger zzz" {
		t.Errorf("got %q", got)
	}

	// Like the Python helper (bytes.lower()), case folding is ASCII only; a
	// non-ASCII needle matches its exact text, even split inside a character.
	m = newMatcher("Ärger", 5)
	line := []byte("x ärger no\nx ÄRGER yes\n")
	cut := bytes.Index(line, []byte("ÄRGER")) + 1 // inside the two bytes of Ä
	_, _ = m.Write(line[:cut])
	_, _ = m.Write(line[cut:])
	if got := m.lines(); len(got) != 1 || got[0] != "x ÄRGER yes" {
		t.Errorf("non-ASCII needle: %q", got)
	}
}

func TestAuditFieldsHoldNoContent(t *testing.T) {
	raw, _ := json.Marshal(InstallUnitRequest{Name: "demo.service", Content: "Environment=PW=hunter2\n[Service]"})
	if got := fmtAttrs(auditInstallUnit(raw)); strings.Contains(got, "hunter2") || !strings.Contains(got, "demo.service") {
		t.Errorf("install audit %s", got)
	}
	raw, _ = json.Marshal(LogsRequest{Source: "journal", Grep: "hunter2"})
	if got := fmtAttrs(auditLogs(raw)); strings.Contains(got, "hunter2") || !strings.Contains(got, "journal") {
		t.Errorf("logs audit %s", got)
	}
}

func fmtAttrs(a []any) string { b, _ := json.Marshal(a); return string(b) }

func TestInstallUnit(t *testing.T) {
	dir := t.TempDir()
	f := &fakeHost{}
	h := f.host(dir)
	unit := "[Unit]\nDescription=demo\n[Service]\nExecStart=/usr/bin/demo\n"

	out, err := call(t, h, "systemd.install_unit", InstallUnitRequest{Name: "demo-app.service", Content: unit})
	if err != nil || string(out) != `{"installed":"demo-app.service","started":true}` {
		t.Fatalf("install = %s, %v", out, err)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "demo-app.service"))
	if string(b) != unit {
		t.Errorf("unit file %q", b)
	}
	if st, _ := os.Stat(filepath.Join(dir, "demo-app.service")); st.Mode().Perm() != 0o644 {
		t.Errorf("mode %v", st.Mode().Perm())
	}
	wantCalls := [][]string{
		{"systemctl", "daemon-reload"},
		{"systemctl", "enable", "--", "demo-app.service"},
		{"systemctl", "restart", "--", "demo-app.service"},
	}
	if len(f.calls) != 3 {
		t.Fatalf("calls %v", f.calls)
	}
	for i := range wantCalls {
		if !slices.Equal(f.calls[i], wantCalls[i]) {
			t.Errorf("call %d = %v", i, f.calls[i])
		}
	}

	f.calls = nil
	out, _ = call(t, h, "systemd.install_unit", InstallUnitRequest{Name: "demo-app.service", Content: unit, NoStart: true})
	if len(f.calls) != 2 || !strings.Contains(string(out), `"started":false`) {
		t.Errorf("no_start: %s %v", out, f.calls)
	}

	bad := []InstallUnitRequest{
		{Name: "meteorcli.service", Content: unit},
		{Name: "meteorcli-helper.service", Content: unit},
		{Name: "demo.timer", Content: unit},
		{Name: "../evil.service", Content: unit},
		{Name: "-demo.service", Content: unit},
		{Name: "Demo.service", Content: unit},
		{Name: "demo.service", Content: "[Unit]\n"},
		{Name: "demo.service", Content: "[Service]\x00"},
		{Name: "demo.service", Content: "[Service]\n" + strings.Repeat("#", maxUnitBytes)},
	}
	for _, r := range bad {
		if _, err := call(t, h, "systemd.install_unit", r); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%q: err = %v", r.Name, err)
		}
	}

	if err := os.Symlink("/etc/passwd", filepath.Join(dir, "link.service")); err != nil {
		t.Fatal(err)
	}
	if _, err := call(t, h, "systemd.install_unit", InstallUnitRequest{Name: "link.service", Content: unit}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("symlink: %v", err)
	}
	f.fail = "enable"
	if _, err := call(t, h, "systemd.install_unit", InstallUnitRequest{Name: "demo2.service", Content: unit}); err == nil {
		t.Error("failing enable: want error")
	}
}

func TestServerServesOnlyAllowedUID(t *testing.T) {
	dir, err := os.MkdirTemp("", "hlp")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	f := &fakeHost{output: "x\n"}

	for _, tt := range []struct {
		allowed uint32
		ok      bool
	}{{uint32(os.Getuid()), true}, {uint32(os.Getuid()) + 1, false}} {
		path := filepath.Join(dir, "h.sock")
		l, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
		if err != nil {
			t.Fatal(err)
		}
		srv := &Server{AllowedUID: tt.allowed, Host: f.host(dir), Log: slog.New(slog.DiscardHandler)}
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() { done <- srv.Serve(ctx, l) }()

		err = NewClient(path).Call(context.Background(), "logs.read", LogsRequest{Source: "journal"}, &LogsReply{})
		if tt.ok && err != nil {
			t.Errorf("allowed uid refused: %v", err)
		}
		if !tt.ok && localrpc.CodeOf(err) != localrpc.CodeDenied {
			t.Errorf("other uid: err = %v, want permission_denied", err)
		}
		cancel()
		if err := <-done; err != nil {
			t.Error(err)
		}
	}
}

func TestListenerFromSystemdNeedsActivation(t *testing.T) {
	t.Setenv("LISTEN_PID", "1")
	if _, err := ListenerFromSystemd(); err == nil {
		t.Error("want error without socket activation")
	}
	t.Setenv("LISTEN_PID", itoa(os.Getpid()))
	t.Setenv("LISTEN_FDS", "0")
	if _, err := ListenerFromSystemd(); err == nil {
		t.Error("want error without LISTEN_FDS")
	}
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func TestLookToolUsesSafePathOnly(t *testing.T) {
	dir := t.TempDir()
	fake := filepath.Join(dir, "systemctl-fake")
	if err := os.WriteFile(fake, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	if _, err := lookTool("systemctl-fake"); err == nil {
		t.Error("found a tool through the process PATH")
	}
	if _, err := lookTool("/bin/echo"); err == nil {
		t.Error("accepted a path")
	}
	if p, err := lookTool("echo"); err != nil || !strings.HasPrefix(p, "/") {
		t.Errorf("echo = %q, %v", p, err)
	}
	var out bytes.Buffer
	if err := streamTool(context.Background(), &out, "echo", "hi"); err != nil || out.String() != "hi\n" {
		t.Errorf("stream = %q, %v", out.String(), err)
	}
	if err := streamTool(context.Background(), &out, "no-such-tool-meteorcli"); err == nil {
		t.Error("missing tool: want error")
	}
	c := &capped{max: 4}
	_, _ = c.Write([]byte("abcdef"))
	if n, _ := c.Write([]byte("gh")); n != 2 || c.String() != "abcd" {
		t.Errorf("capped %q", c.String())
	}
}

func TestRunToolUsesArgList(t *testing.T) {
	out, err := runTool(context.Background(), "echo", "a; rm -rf /", "$HOME")
	if err != nil || string(out) != "a; rm -rf / $HOME\n" {
		t.Errorf("echo = %q, %v", out, err)
	}
	if _, err := runTool(context.Background(), "false"); err == nil {
		t.Error("false: want error")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := runTool(ctx, "sleep", "5"); err == nil {
		t.Error("cancelled: want error")
	}
	if SystemHost().UnitDir != "/etc/systemd/system" {
		t.Error("unit dir")
	}
}
