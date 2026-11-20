// Package agenttest builds agent.Deps for module tests: an enrolled device
// (id "d-1", token "dev_1") in a temporary root, talking to a fake control
// plane, with a quiet logger and a host that reads nothing real. Never used
// outside tests.
package agenttest

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/platform"
	"meteorcloud/meteorcli/internal/state"
)

// DeviceID and Token are the identity Deps sets up.
const (
	DeviceID = "d-1"
	Token    = "dev_1"
)

// MQTT, when given to Deps, is stored as identity/mqtt.json.
type MQTT struct {
	Addr   string // host:port
	CAFile string
}

// Deps returns Deps for an enrolled device against cp. files maps paths under
// the host's root to content (for /proc readings).
func Deps(t *testing.T, cp *fakecp.Server, mq *MQTT, files map[string]string) agent.Deps {
	t.Helper()
	p := config.DefaultPaths(t.TempDir())
	must(t, os.MkdirAll(p.IdentityDir, 0o700))
	must(t, (&config.Device{DeviceID: DeviceID, HeartbeatIntervalSeconds: 60}).Save(p.DeviceFile(), config.Owner{}))
	must(t, os.WriteFile(p.DeviceTokenFile(), []byte(Token), 0o600))
	if mq != nil {
		host, port, err := net.SplitHostPort(mq.Addr)
		must(t, err)
		n, _ := strconv.Atoi(port)
		data, _ := json.Marshal(map[string]any{
			"version": 1, "host": host, "port": n, "username": "device_d-1", "password": "mqtt_pw",
			"tls": true, "ca_cert_path": mq.CAFile, "config_version": "v1",
		})
		must(t, os.WriteFile(p.MQTTFile(), data, 0o600))
	}
	src := identity.NewSource(p)
	must(t, src.Load())
	client, err := api.New(api.Options{BaseURL: cp.URL, Version: "1.2.3", HTTPClient: cp.Client()})
	must(t, err)
	outbox, err := state.NewOutbox(filepath.Join(p.StateDir, "outbox"), 0)
	must(t, err)
	root := t.TempDir()
	for name, content := range files {
		path := filepath.Join(root, name)
		must(t, os.MkdirAll(filepath.Dir(path), 0o755))
		must(t, os.WriteFile(path, []byte(content), 0o644))
	}
	errs := &agent.ErrorCounter{}
	log := agent.NewCountingLogger(io.Discard, slog.LevelDebug, errs)
	return agent.Deps{
		Version: "1.2.3", Paths: p, API: client, Identity: src, Outbox: outbox,
		Health: agent.NewHealth(), Signals: agent.NewSignals(), Log: log,
		Bus: bus.NewRouter(log), Errors: errs,
		Host: &platform.Host{
			Root:     root,
			Hostname: func() (string, error) { return "edge", nil },
			Uname:    func() (string, string) { return "6.1", "aarch64" },
			LookPath: func(n string) (string, error) {
				if n == "docker" {
					return "/usr/bin/docker", nil
				}
				return "", errors.New("no")
			},
			Run:    func(context.Context, string, ...string) bool { return true },
			IPv4:   func(string) []net.IP { return nil },
			Statfs: func(string) (uint64, uint64, uint64, bool) { return 0, 0, 0, false },
		},
	}
}

// Start runs m in the background until the test ends.
func Start(t *testing.T, m agent.Module, d agent.Deps) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := m.Run(ctx, d); err != nil {
			t.Errorf("%s.Run: %v", m.Name(), err)
		}
	}()
	t.Cleanup(func() { cancel(); wg.Wait() })
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
