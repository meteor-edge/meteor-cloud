package cli

import (
	"context"
	"flag"
	"io"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/helper"
)

// fakeHelper serves the helper's real operations on the agent's helper socket,
// over fake tools: a unit list and a journal.
func fakeHelper(t *testing.T, root string) *[]string {
	t.Helper()
	var ran []string
	run := func(_ context.Context, name string, args ...string) ([]byte, error) {
		ran = append(ran, strings.Join(append([]string{name}, args...), " "))
		switch name {
		case "systemctl":
			return []byte("nginx.service loaded active running Web\n"), nil
		case "journalctl":
			return []byte("first line\nERROR second\n"), nil
		}
		return nil, nil
	}
	stream := func(ctx context.Context, w io.Writer, name string, args ...string) error {
		out, err := run(ctx, name, args...)
		if err == nil {
			_, err = w.Write(out)
		}
		return err
	}
	h := helper.Host{Run: run, Stream: stream, StreamAll: stream, LogGlobs: []string{}, DockerSocket: filepath.Join(root, "none"),
		SystemdDir: root}
	path := config.DefaultPaths(root).HelperSocket()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	l, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	s := &helper.Server{AllowedUID: uint32(os.Getuid()), Host: h, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	go func() { _ = s.Serve(ctx, l); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
	return &ran
}

func TestLogsThroughTheAgent(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads logs in-process")
	}
	root := runningAgent(t, groupsOf{agent.GroupRead})
	ran := fakeHelper(t, root)

	r := meteorcli(t, root, nil, "logs")
	if r.code != 0 || !strings.Contains(r.stdout, "journal") || !strings.Contains(r.stdout, "systemd:nginx.service") {
		t.Fatalf("list = %+v", r)
	}
	r = meteorcli(t, root, nil, "logs", "list")
	if r.code != 0 || !strings.HasPrefix(r.stdout, `[{"kind":"journal","name":""}`) {
		t.Fatalf("list (helper syntax) = %+v", r)
	}
	r = meteorcli(t, root, nil, "logs", "systemd:nginx.service", "-n", "5", "--grep", "error")
	if r.code != 0 || r.stdout != "ERROR second\n" {
		t.Fatalf("show = %+v", r)
	}
	r = meteorcli(t, root, nil, "logs", "show", "systemd", "nginx.service", "--lines", "7", "--since", "1h")
	if r.code != 0 || r.stdout != "first line\nERROR second\n" {
		t.Fatalf("show (helper syntax) = %+v", r)
	}
	if !slices.ContainsFunc(*ran, func(c string) bool { return strings.Contains(c, "--lines=7 --unit=nginx.service --since=-1h") }) {
		t.Errorf("journalctl runs %q", *ran)
	}
	if r := meteorcli(t, root, nil, "logs", "systemd:ssh.service"); r.code != 2 || !strings.Contains(r.stderr, "not") {
		t.Errorf("unlisted unit = %+v", r)
	}
}

func TestLogsArguments(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"logs", "a", "b"},
		{"logs", "journal", "--lines", "-1"},
		{"logs", "journal", "--since", "soon"},
		{"logs", "show"},
		{"logs", "show", "kernel"},
		{"logs", "list", "x"},
		{"logs", "--nope"},
	} {
		if r := meteorcli(t, root, nil, args...); r.code != 2 {
			t.Errorf("%q = %+v", args, r)
		}
	}
	if os.Geteuid() != 0 {
		if r := meteorcli(t, root, nil, "logs"); r.code != 1 || !strings.Contains(r.stderr, "not running") {
			t.Errorf("without an agent = %+v", r)
		}
	}
}

func TestParseInterspersed(t *testing.T) {
	fl := flag.NewFlagSet("x", flag.ContinueOnError)
	n := fl.Int("n", 0, "")
	f := fl.Bool("f", false, "")
	pos, code, done := parseInterspersed(fl, []string{"-n", "3", "docker:web", "-f"})
	if done || code != 0 || *n != 3 || !*f || !slices.Equal(pos, []string{"docker:web"}) {
		t.Errorf("pos %q n %d f %v", pos, *n, *f)
	}
}

func TestConfigSSHUser(t *testing.T) {
	root := t.TempDir()
	if r := meteorcli(t, root, nil, "config", "--ssh-user", "Bad User"); r.code != 2 {
		t.Errorf("bad user = %+v", r)
	}
	r := meteorcli(t, root, nil, "config", "--domain", "a.example", "--ssh-user", "pi")
	if r.code != 0 || !strings.Contains(r.stdout, "SSH user:       pi") {
		t.Fatalf("config = %+v", r)
	}
	// Changing only the user keeps the server and its enrollment.
	p := config.DefaultPaths(root)
	write(t, p.DeviceTokenFile(), "dev_x")
	if r := meteorcli(t, root, nil, "config", "--ssh-user", "admin"); r.code != 0 || strings.Contains(r.stdout, "Cleared") {
		t.Fatalf("user only = %+v", r)
	}
	c, err := config.Load(p.ConfigFile)
	if err != nil || c.SSHUser != "admin" || c.Server() != "https://a.example" {
		t.Errorf("config %+v %v", c, err)
	}
	if show := meteorcli(t, root, nil, "config", "--show"); !strings.Contains(show.stdout, "SSH user:       admin") {
		t.Errorf("show = %+v", show)
	}
}
