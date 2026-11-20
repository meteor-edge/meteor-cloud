package telemetry

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"

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

func TestToolsAreCached(t *testing.T) {
	h := host(t, nil)
	probes := 0
	h.LookPath = func(name string) (string, error) {
		if name == "docker" {
			probes++
			return "/usr/bin/docker", nil
		}
		return "", errors.New("not found")
	}
	tools := &Tools{Host: h}
	for range 3 {
		if !tools.Get(context.Background()).Docker {
			t.Fatal("docker not seen")
		}
	}
	if probes != 1 {
		t.Errorf("probed %d times, want 1", probes)
	}
}
