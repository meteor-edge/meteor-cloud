package telemetry

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/platform"
)

func host(t *testing.T, files map[string]string) *platform.Host {
	t.Helper()
	root := t.TempDir()
	for p, content := range files {
		full := filepath.Join(root, p)
		_ = os.MkdirAll(filepath.Dir(full), 0o755)
		if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return &platform.Host{
		Root:     root,
		Hostname: func() (string, error) { return "", errors.New("no hostname") },
		Uname:    func() (string, string) { return "6.1.0", "x86_64" },
		LookPath: func(string) (string, error) { return "", errors.New("not found") },
		Run:      func(context.Context, string, ...string) bool { return false },
		IPv4:     func(string) []net.IP { return nil },
		Statfs:   func(string) (uint64, uint64, uint64, bool) { return 0, 0, 0, false },
	}
}

// The inventory always carries every contract key, null when unknown, as the
// Python agent did; recordings depend on the keys being there.
func TestInventoryHasEveryKey(t *testing.T) {
	inv := Inventory(host(t, map[string]string{"/proc/meminfo": "MemTotal: 2048000 kB\n"}), "1.2.3")
	keys := []string{"serial_number", "mac_addresses", "hostname", "os_name", "os_version", "kernel_version",
		"architecture", "hardware_model", "cpu_model", "cpu_cores", "memory_mb", "local_ip", "agent_version"}
	if len(inv) != len(keys) {
		t.Errorf("inventory has %d keys, want %d: %v", len(inv), len(keys), inv)
	}
	for _, k := range keys {
		if _, ok := inv[k]; !ok {
			t.Errorf("missing %s", k)
		}
	}
	if inv["hostname"] != nil || inv["serial_number"] != nil || inv["local_ip"] != nil {
		t.Errorf("unknown values must be null: %v", inv)
	}
	if inv["kernel_version"] != "6.1.0" || inv["architecture"] != "x86_64" || inv["agent_version"] != "1.2.3" ||
		inv["memory_mb"] != 2000 {
		t.Errorf("inventory %v", inv)
	}
	if macs, ok := inv["mac_addresses"].([]string); !ok || macs == nil {
		t.Errorf("mac_addresses must be an array, got %#v", inv["mac_addresses"])
	}
}

func TestMetrics(t *testing.T) {
	h := host(t, map[string]string{
		"/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 333 kB\n",
		"/proc/loadavg": "0.123 0.456 0.789 1/1 1\n",
		"/proc/uptime":  "61.9 1\n",
	})
	h.Statfs = func(string) (uint64, uint64, uint64, bool) { return 3000, 1000, 900, true }
	m := Metrics(context.Background(), h)
	want := map[string]any{
		"memory_percent": 66.7, "memory_used_bytes": uint64(667 * 1024), "memory_total_bytes": uint64(1000 * 1024),
		"disk_percent": 66.7, "disk_used_bytes": uint64(2000), "disk_total_bytes": uint64(3000), // free 1000
		"load_1m": 0.12, "load_5m": 0.46, "load_15m": 0.79, "uptime_seconds": 61, "temperature_c": nil,
	}
	for k, v := range want {
		if m[k] != v {
			t.Errorf("%s = %#v, want %#v", k, m[k], v)
		}
	}
	if _, ok := m["cpu_percent"]; ok {
		t.Error("cpu_percent without /proc/stat")
	}
}

// A complete probe is reused: the Docker CLI starts once per toolsTTL.
func TestToolsAreCached(t *testing.T) {
	h := host(t, nil)
	h.LookPath = func(name string) (string, error) {
		if name == "docker" {
			return "/usr/bin/docker", nil
		}
		return "", errors.New("not found")
	}
	runs := 0
	h.Run = func(context.Context, string, ...string) bool { runs++; return true }
	tools := &Tools{Host: h}
	for range 3 {
		if got := tools.Get(context.Background()); !got.Docker || !got.DockerCompose {
			t.Fatalf("docker and compose not seen: %+v", got)
		}
	}
	if runs != 1 {
		t.Errorf("docker compose version ran %d times, want 1", runs)
	}
}

// Docker and Compose installed while the agent runs are reported within
// missingTTL, not toolsTTL: the control plane skips a deployment whose runtime
// the device last reported missing.
func TestInstallingDockerShowsUpSoon(t *testing.T) {
	h := host(t, nil)
	docker := false
	h.LookPath = func(name string) (string, error) {
		if name == "docker" && docker {
			return "/usr/bin/docker", nil
		}
		return "", errors.New("not found")
	}
	compose := false
	runs := 0
	h.Run = func(context.Context, string, ...string) bool { runs++; return compose }
	tools := &Tools{Host: h}
	ctx := context.Background()
	if tools.Get(ctx).Docker {
		t.Fatal("docker seen before it is installed")
	}

	docker = true // apt install docker.io: the binary appears
	if got := tools.Get(ctx); !got.Docker || got.DockerCompose {
		t.Fatalf("after installing docker: %+v", got)
	}
	compose = true // apt install docker-compose-plugin
	if tools.Get(ctx).DockerCompose {
		t.Fatal("compose probed again before missingTTL")
	}
	tools.at = tools.at.Add(-missingTTL - time.Second)
	if !tools.Get(ctx).DockerCompose {
		t.Fatal("compose not seen after missingTTL")
	}
	tools.at = tools.at.Add(-missingTTL - time.Second)
	tools.Get(ctx)
	if runs != 2 {
		t.Errorf("docker compose version ran %d times, want 2 (complete probes keep toolsTTL)", runs)
	}

	docker = false // docker removed
	if got := tools.Get(ctx); got.Docker || got.DockerCompose {
		t.Fatalf("after removing docker: %+v", got)
	}
}
