package agent

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestSignalsLatestWinsAndNeverBlocks(t *testing.T) {
	hub := NewSignals()
	all := hub.Subscribe(nil)
	defer all.Close()
	actions := hub.Subscribe(Is(WorkWaiting{Kind: "actions"}))
	defer actions.Close()

	// Nobody reads: publishing must still return at once.
	for range 1000 {
		hub.Publish(WorkWaiting{Kind: "actions"})
	}
	hub.Publish(WorkWaiting{Kind: "wifi"})
	hub.Publish(Enrolled{})
	hub.Publish(Enrolled{})

	<-all.C()
	got := all.Take()
	if len(got) != 3 || got[0].Key() != "work_waiting:actions" || got[1].Key() != "work_waiting:wifi" || got[2] != (Enrolled{}) {
		t.Errorf("all got %v", got)
	}
	if len(all.Take()) != 0 {
		t.Error("Take did not clear")
	}
	<-actions.C()
	if got := actions.Take(); len(got) != 1 {
		t.Errorf("filtered got %v", got)
	}

	actions.Close()
	hub.Publish(WorkWaiting{Kind: "actions"})
	select {
	case <-actions.C():
		t.Error("closed subscription still notified")
	default:
	}
	for _, s := range []Signal{CredentialsRotated{}, ConfigChanged{}} {
		if s.Key() == "" {
			t.Error("empty key")
		}
	}
}

func TestHealthSnapshotAndAlive(t *testing.T) {
	h := NewHealth()
	now := time.Unix(1000, 0)
	h.now = func() time.Time { return now }
	h.register("heartbeat", true)
	h.register("deploy", false)
	h.Set("heartbeat", StateRunning, nil)
	h.failed("deploy", errors.New("pull failed: Bearer dev_abcdefghijklmnopqrstuvwxyz "+strings.Repeat("x", 600)))

	snap := h.Snapshot()
	if snap["heartbeat"].State != "running" || snap["deploy"].Restarts != 1 {
		t.Errorf("snapshot %+v", snap)
	}
	msg := *snap["deploy"].LastError
	if len(msg) != maxErrorLen || strings.Contains(msg, "dev_abc") {
		t.Errorf("last_error not redacted or cut: %q", msg[:60])
	}
	if strings.Join(h.Names(), ",") != "deploy,heartbeat" {
		t.Errorf("names %v", h.Names())
	}

	if !h.Alive(time.Minute) {
		t.Error("fresh module not alive")
	}
	now = now.Add(2 * time.Minute)
	if h.Alive(time.Minute) {
		t.Error("stale critical module counted alive")
	}
	h.Tick("heartbeat")
	if !h.Alive(time.Minute) {
		t.Error("Tick did not refresh")
	}
	if _, ok := h.Get("missing"); ok {
		t.Error("Get(missing) ok")
	}
}

func TestRedact(t *testing.T) {
	tests := []struct{ in, mustNot string }{
		{"Authorization: Bearer dev_s3cr3t", "s3cr3t"},
		{`{"device_token": "abc123", "x": 1}`, "abc123"},
		{"password=hunter2&user=pi", "hunter2"},
		{"psk: wifi-pass", "wifi-pass"},
		{"got key_AbCdEfGhIjKlMnOpQrStUvWxYz012345 from file", "AbCdEfGhIjKl"},
		{"mqtt_" + strings.Repeat("Z", 43), "ZZZZ"},
	}
	for _, tt := range tests {
		if got := Redact(tt.in); strings.Contains(got, tt.mustNot) || !strings.Contains(got, redacted) {
			t.Errorf("Redact(%q) = %q", tt.in, got)
		}
	}
	if got := Redact("key prefix key_contract shown"); got != "key prefix key_contract shown" {
		t.Errorf("display prefix redacted: %q", got)
	}
}

func TestLoggerRedactsAttrs(t *testing.T) {
	var buf bytes.Buffer
	log := NewLogger(&buf, slog.LevelInfo).With("module", "identity", "api_key", "key_secret")
	log.Info("enrolled with Bearer abc",
		"device_token", "dev_x", "password", "p", "err", errors.New("token=zzz"),
		slog.Group("mqtt", "password", "q", "host", "broker"), "count", 3, "token_present", true)
	out := buf.String()
	for _, secret := range []string{"key_secret", "dev_x", "=p ", "zzz", "abc", "mqtt.password=q"} {
		if strings.Contains(out, secret) {
			t.Errorf("log leaks %q: %s", secret, out)
		}
	}
	for _, keep := range []string{"module=identity", "mqtt.host=broker", "count=3", "token_present=true"} {
		if !strings.Contains(out, keep) {
			t.Errorf("log lost %q: %s", keep, out)
		}
	}
	if strings.Contains(out, "time=") {
		t.Error("time should be left to journald")
	}
}

func TestNotifier(t *testing.T) {
	dir, err := os.MkdirTemp("", "sdn")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	sock := filepath.Join(dir, "n.sock")
	conn, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: sock, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	t.Setenv("NOTIFY_SOCKET", sock)
	t.Setenv("WATCHDOG_USEC", "60000000")
	t.Setenv("WATCHDOG_PID", strconv.Itoa(os.Getpid()))
	n := NotifierFromEnv()
	if n.WatchdogInterval() != time.Minute {
		t.Errorf("watchdog %v", n.WatchdogInterval())
	}
	ctx := context.Background()
	for _, send := range []func(context.Context) error{n.Ready, n.Watchdog, n.Stopping} {
		if err := send(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if err := n.Status(ctx, "waiting for enrollment"); err != nil {
		t.Fatal(err)
	}
	var got []string
	buf := make([]byte, 256)
	for range 4 {
		_ = conn.SetReadDeadline(time.Now().Add(time.Second))
		k, err := conn.Read(buf)
		if err != nil {
			t.Fatal(err)
		}
		got = append(got, string(buf[:k]))
	}
	if strings.Join(got, "|") != "READY=1|WATCHDOG=1|STOPPING=1|STATUS=waiting for enrollment" {
		t.Errorf("got %v", got)
	}

	t.Setenv("WATCHDOG_PID", "1")
	if NotifierFromEnv().WatchdogInterval() != 0 {
		t.Error("watchdog meant for another pid")
	}
	var none *Notifier
	if none.Ready(ctx) != nil || none.WatchdogInterval() != 0 {
		t.Error("nil notifier should do nothing")
	}
}

func TestSupervisorFeedsWatchdogOnlyWhileTicking(t *testing.T) {
	dir, err := os.MkdirTemp("", "sdw")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	sock := filepath.Join(dir, "n.sock")
	conn, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: sock, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	stuck := make(chan struct{})
	mod := &fakeModule{name: "heartbeat", critical: true, run: func(ctx context.Context, d Deps, _ int32) error {
		t := time.NewTicker(time.Millisecond)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return nil
			case <-stuck:
				<-ctx.Done() // stops ticking
				return nil
			case <-t.C:
				d.Health.Tick("heartbeat")
			}
		}
	}}
	d := testDeps(nil)
	s := fast(NewSupervisor([]Module{mod}, d))
	s.Notifier = &Notifier{socket: sock, watchdog: 20 * time.Millisecond}
	s.MaxTickAge = 30 * time.Millisecond
	stop := start(t, s)
	defer stop()

	read := func(timeout time.Duration) string {
		buf := make([]byte, 64)
		_ = conn.SetReadDeadline(time.Now().Add(timeout))
		k, err := conn.Read(buf)
		if err != nil {
			return ""
		}
		return string(buf[:k])
	}
	// The watchdog loop starts before READY=1 is sent, so either may come first.
	seen := map[string]bool{}
	for range 10 {
		if got := read(time.Second); got != "" {
			seen[got] = true
		}
		if seen["READY=1"] && seen["WATCHDOG=1"] {
			break
		}
	}
	if !seen["READY=1"] || !seen["WATCHDOG=1"] {
		t.Fatalf("startup messages %v, want READY=1 and WATCHDOG=1", seen)
	}
	close(stuck)
	time.Sleep(60 * time.Millisecond)
	for read(5*time.Millisecond) != "" { // drain pings sent before the stall was seen
	}
	if got := read(100 * time.Millisecond); got != "" {
		t.Errorf("watchdog still fed while stuck: %q", got)
	}
}

func TestRedactQuotedValues(t *testing.T) {
	tests := []struct{ in, mustNot string }{
		{"password='correct horse'", "horse"},
		{`"password": "correct horse battery"`, "battery"},
		{`"psk": "with \"escaped\" quote"`, "escaped"},
		{"token=plain next=1", "plain"},
	}
	for _, tt := range tests {
		if got := Redact(tt.in); strings.Contains(got, tt.mustNot) {
			t.Errorf("Redact(%q) = %q", tt.in, got)
		}
	}
	if got := Redact("token=plain next=1"); !strings.Contains(got, "next=1") {
		t.Errorf("redacted too much: %q", got)
	}
}

func TestLoggerRedactsInsideSensitiveGroups(t *testing.T) {
	var buf bytes.Buffer
	log := NewLogger(&buf, slog.LevelInfo)
	log.WithGroup("credentials").Info("a", "value", "hunter2")
	log.WithGroup("credentials").With("user", "hunter3").Info("b")
	log.Info("c", slog.Group("credentials", "value", "hunter4"))
	log.WithGroup("mqtt").Info("d", "host", "broker")
	out := buf.String()
	for _, secret := range []string{"hunter2", "hunter3", "hunter4"} {
		if strings.Contains(out, secret) {
			t.Errorf("log leaks %q: %s", secret, out)
		}
	}
	if !strings.Contains(out, "mqtt.host=broker") {
		t.Errorf("unrelated group redacted: %s", out)
	}
}
