package cli

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

type result struct {
	code           int
	stdout, stderr string
}

// meteorcli runs the CLI against a scratch root with a fake environment.
func meteorcli(t *testing.T, root string, env map[string]string, args ...string) result {
	t.Helper()
	var out, errOut bytes.Buffer
	e := &Env{
		Version: "9.9.9", Stdout: &out, Stderr: &errOut,
		Getenv:       func(k string) string { return env[k] },
		ServiceState: func(context.Context) string { return "active" },
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	code := run(ctx, e, append([]string{"--root", root}, args...))
	return result{code, out.String(), errOut.String()}
}

func write(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

// pythonLayout is /etc/meteorcli as the Python agent leaves it after enrolling.
func pythonLayout(t *testing.T, root string) {
	t.Helper()
	etc := filepath.Join(root, "etc", "meteorcli")
	write(t, filepath.Join(etc, "config.json"), `{"server_url": "https://meteor.example", "device_id": "dev-uuid-1",
 "organization_id": "org-uuid-2", "name": "edge-01", "heartbeat_interval_seconds": 30,
 "domain": "meteor.example", "api_base": null}`)
	write(t, filepath.Join(etc, "device-token"), "dev_token_secret_value")
	write(t, filepath.Join(etc, "mqtt.json"), `{"host":"mqtt.example","port":8883,"username":"u","password":"mqtt_pw","tls":true,"ca_cert_path":null}`)
	write(t, filepath.Join(etc, "wireguard", "public.key"), "pub")
	write(t, filepath.Join(etc, "wireguard", "private.key"), "priv")
}

func TestStatusReadsConfigImportedFromPython(t *testing.T) {
	root := t.TempDir()
	pythonLayout(t, root)

	before := meteorcli(t, root, nil, "status")
	if before.code != 1 || !strings.Contains(before.stdout, "meteorcli config import") {
		t.Fatalf("status before import = %+v", before)
	}

	imp := meteorcli(t, root, nil, "config", "import")
	if imp.code != 0 || !strings.Contains(imp.stdout, "imported  device-token") {
		t.Fatalf("import = %+v", imp)
	}

	st := meteorcli(t, root, nil, "status")
	if st.code != 0 {
		t.Fatalf("status = %+v", st)
	}
	for _, want := range []string{
		"Domain:         meteor.example",
		"API base:       https://meteor.example",
		"API key:        MISSING",
		"Device ID:      dev-uuid-1",
		"Organization:   org-uuid-2",
		"Name:           edge-01",
		"Heartbeat:      every 30s",
		"Credential:     present",
		"Service:        meteorcli.service is active",
		"MQTT:           configured",
		"WireGuard:      key pair present",
	} {
		if !strings.Contains(st.stdout, want+"\n") {
			t.Errorf("status lacks %q:\n%s", want, st.stdout)
		}
	}
	if strings.Contains(st.stdout+st.stderr, "dev_token_secret_value") || strings.Contains(st.stdout, "mqtt_pw") {
		t.Error("status printed a secret")
	}

	again := meteorcli(t, root, nil, "config", "import")
	if again.code != 0 || !strings.Contains(again.stdout, "Nothing to import") {
		t.Errorf("second import = %+v", again)
	}
}

func TestStatusNotConfigured(t *testing.T) {
	r := meteorcli(t, t.TempDir(), nil, "status")
	if r.code != 1 || !strings.Contains(r.stdout, "Not configured") {
		t.Errorf("%+v", r)
	}
}

func TestConfigSetShowAndServerChange(t *testing.T) {
	root := t.TempDir()
	r := meteorcli(t, root, nil, "config", "--domain", "a.example", "--api-key", `"Bearer key_abc"`)
	if r.code != 0 || !strings.Contains(r.stdout, "API:            https://a.example") {
		t.Fatalf("config = %+v", r)
	}
	p := config.DefaultPaths(root)
	if b, _ := os.ReadFile(p.APIKeyFile()); string(b) != "key_abc" {
		t.Errorf("api key file %q", b)
	}

	show := meteorcli(t, root, nil, "config", "--show")
	if show.code != 0 || !strings.Contains(show.stdout, "API key:        present") || strings.Contains(show.stdout, "key_abc") {
		t.Errorf("show = %+v", show)
	}

	// Pretend the device enrolled, then point it at another server.
	write(t, p.DeviceTokenFile(), "dev_x")
	write(t, p.DeviceFile(), `{"version":1,"device_id":"d1"}`)
	r = meteorcli(t, root, nil, "config", "--domain", "b.example")
	if r.code != 0 || !strings.Contains(r.stdout, "Cleared previous enrollment (device-token, api-key, device.json)") {
		t.Errorf("server change = %+v", r)
	}
	if _, err := os.Stat(p.DeviceTokenFile()); err == nil {
		t.Error("old token kept after a server change")
	}

	r = meteorcli(t, root, map[string]string{"METEORCLI_DOMAIN": "192.168.0.10"}, "config")
	if r.code != 0 || !strings.Contains(r.stderr, "port 8000") {
		t.Errorf("LAN hint = %+v", r)
	}
}

func TestConfigErrors(t *testing.T) {
	root := t.TempDir()
	if r := meteorcli(t, root, nil, "config"); r.code != 2 {
		t.Errorf("no flags: %+v", r)
	}
	if r := meteorcli(t, root, nil, "config", "--nope"); r.code != 2 {
		t.Errorf("bad flag: %+v", r)
	}
	pythonLayout(t, root)
	if r := meteorcli(t, root, nil, "config", "--domain", "x.example"); r.code != 1 || !strings.Contains(r.stderr, "config import") {
		t.Errorf("config over a Python layout: %+v", r)
	}
	if r := meteorcli(t, root, nil, "config", "--show"); r.code != 1 || !strings.Contains(r.stderr, "config import") {
		t.Errorf("config --show over a Python layout: %+v", r)
	}
}

func TestConfigDropsEnrollmentOfUnknownServer(t *testing.T) {
	root := t.TempDir()
	p := config.DefaultPaths(root)
	// A token survived an import whose config.json was damaged: its server is unknown.
	write(t, p.DeviceTokenFile(), "dev_old")
	r := meteorcli(t, root, nil, "config", "--domain", "new.example")
	if r.code != 0 || !strings.Contains(r.stdout, "Cleared previous enrollment (device-token)") {
		t.Errorf("config = %+v", r)
	}
	if _, err := os.Stat(p.DeviceTokenFile()); err == nil {
		t.Error("token of an unknown server kept")
	}

	// A first configuration without enrollment files still works.
	r = meteorcli(t, t.TempDir(), nil, "config", "--domain", "a.example")
	if r.code != 0 || strings.Contains(r.stdout, "Cleared") {
		t.Errorf("first config = %+v", r)
	}
}

func TestVersionHelpAndUnknown(t *testing.T) {
	root := t.TempDir()
	for _, flag := range []string{"--version", "-V"} {
		if r := meteorcli(t, root, nil, flag); r.code != 0 || r.stdout != "meteorcli 9.9.9\n" {
			t.Errorf("%s = %+v", flag, r)
		}
	}
	if r := meteorcli(t, root, nil, "help"); r.code != 0 || !strings.Contains(r.stdout, "agent (run)") {
		t.Errorf("help = %+v", r)
	}
	if r := meteorcli(t, root, nil, "frobnicate"); r.code != 2 {
		t.Errorf("unknown = %+v", r)
	}
	if r := meteorcli(t, root, nil); r.code != 2 {
		t.Errorf("no command = %+v", r)
	}
	if r := meteorcli(t, root, nil, "status", "extra"); r.code != 2 {
		t.Errorf("extra arg = %+v", r)
	}
}

func TestAgentRunsUntilCancelled(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "etc", "meteorcli", "config.json"), `{"version":1,"domain":"meteor.example"}`)
	var out, errOut bytes.Buffer
	e := &Env{Version: "9.9.9", Stdout: &out, Stderr: &errOut, Getenv: func(string) string { return "" }}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if code := run(ctx, e, []string{"--root", root, "run"}); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut.String())
	}
	logs := errOut.String()
	for _, want := range []string{"module=agent", "server=https://meteor.example", "all modules stopped"} {
		if !strings.Contains(logs, want) {
			t.Errorf("logs lack %q:\n%s", want, logs)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "var", "lib", "meteorcli", "state", "outbox")); err != nil {
		t.Error("outbox not created")
	}
}

func TestParseSystemctlShow(t *testing.T) {
	tests := map[string]string{
		"LoadState=loaded\nActiveState=active\n":      "active",
		"LoadState=not-found\nActiveState=inactive\n": "not installed",
		"LoadState=loaded\n":                          "unknown",
		"":                                            "not installed",
	}
	for in, want := range tests {
		if got := parseSystemctlShow(in); got != want {
			t.Errorf("parse(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestNormalizeSecret(t *testing.T) {
	tests := map[string]string{
		" key_x ":         "key_x",
		`"key_x"`:         "key_x",
		"Bearer key_x":    "key_x",
		"'bearer  key_x'": "key_x",
		"":                "",
	}
	for in, want := range tests {
		if got := normalizeSecret(in); got != want {
			t.Errorf("normalizeSecret(%q) = %q", in, got)
		}
	}
}
