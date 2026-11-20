package platform

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeHost builds a Host over a scratch tree of files (path -> content).
func fakeHost(t *testing.T, files map[string]string) *Host {
	t.Helper()
	root := t.TempDir()
	for p, content := range files {
		full := filepath.Join(root, p)
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return &Host{
		Root:     root,
		Hostname: func() (string, error) { return "edge-1", nil },
		Uname:    func() (string, string) { return "6.6.31", "aarch64" },
		LookPath: func(string) (string, error) { return "", errors.New("not found") },
		Run:      func(context.Context, string, ...string) bool { return false },
		IPv4:     func(string) []net.IP { return nil },
		Statfs:   func(string) (uint64, uint64, uint64, bool) { return 0, 0, 0, false },
	}
}

// piCPUInfo is /proc/cpuinfo of a 64-bit Raspberry Pi 5 kernel: no model name.
const piCPUInfo = `processor	: 0
BogoMIPS	: 108.00
CPU implementer	: 0x41
CPU part	: 0xd0b

processor	: 1
CPU implementer	: 0x41
CPU part	: 0xd0b

Revision	: d04170
Serial		: 0000000000000000
Model		: Raspberry Pi 5 Model B Rev 1.0
`

func TestRaspberryPiInventory(t *testing.T) {
	h := fakeHost(t, map[string]string{
		"/etc/os-release":                             "PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"12\"\n",
		"/proc/cpuinfo":                               piCPUInfo,
		"/proc/meminfo":                               "MemTotal:        8245312 kB\nMemAvailable:    6000000 kB\n",
		"/sys/firmware/devicetree/base/model":         "Raspberry Pi 5 Model B Rev 1.0\x00",
		"/sys/firmware/devicetree/base/serial-number": "a1b2c3d4e5f60718\x00",
		"/sys/class/net/wlan0/address":                "d8:3a:dd:00:00:02\n",
		"/sys/class/net/eth0/address":                 "d8:3a:dd:00:00:01\n",
		"/sys/class/net/docker0/address":              "02:42:00:00:00:01\n",
		"/sys/class/net/lo/address":                   "00:00:00:00:00:00\n",
		"/sys/class/thermal/thermal_zone0/type":       "something-else\n",
		"/sys/class/thermal/thermal_zone0/temp":       "40000\n",
		"/sys/class/thermal/thermal_zone1/type":       "cpu-thermal\n",
		"/sys/class/thermal/thermal_zone1/temp":       "51234\n",
	})
	h.IPv4 = func(iface string) []net.IP {
		switch iface {
		case "eth0":
			return []net.IP{net.ParseIP("169.254.3.4").To4()} // link-local: skipped
		case "wlan0":
			return []net.IP{net.ParseIP("192.168.1.20").To4()}
		}
		return []net.IP{net.ParseIP("172.17.0.1").To4()}
	}
	if name, version := h.OSRelease(); name != "Debian GNU/Linux" || version != "12" {
		t.Errorf("os = %q %q", name, version)
	}
	if model, cores := h.CPU(); model != "ARM Cortex-A76" || cores != 2 {
		t.Errorf("cpu = %q %d", model, cores)
	}
	if got := h.SerialNumber(); got != "a1b2c3d4e5f60718" {
		t.Errorf("serial = %q (the all-zero cpuinfo serial must be skipped)", got)
	}
	if got := h.HardwareModel(); got != "Raspberry Pi 5 Model B Rev 1.0" {
		t.Errorf("model = %q", got)
	}
	if got := h.MACAddresses(); len(got) != 1 || got[0] != "d8:3a:dd:00:00:01" {
		t.Errorf("mac = %v (wired first, virtual skipped)", got)
	}
	if got := h.LocalIPv4(); got != "192.168.1.20" {
		t.Errorf("local ip = %q", got)
	}
	if got := h.MemoryMB(); got != 8052 {
		t.Errorf("memory = %d", got)
	}
	if b := h.Board(); b.Name != "raspberry-pi" {
		t.Errorf("board = %s", b.Name)
	}
	if temp, ok := h.TemperatureC(); !ok || temp != 51.234 {
		t.Errorf("temperature = %v %v (cpu-thermal zone first)", temp, ok)
	}
}

func TestOSReleaseQuoting(t *testing.T) {
	tests := map[string][2]string{
		"NAME='Acme Linux'\nVERSION_ID='1.0'\n":           {"Acme Linux", "1.0"},
		"NAME=\"Say \\\"hi\\\" \\$HOME\"\nVERSION_ID=2\n": {`Say "hi" $HOME`, "2"},
		"NAME=Plain\\ Name\nVERSION=\"3 (x)\"\n":          {"Plain Name", "3 (x)"},
	}
	for content, want := range tests {
		h := fakeHost(t, map[string]string{"/etc/os-release": content})
		if name, version := h.OSRelease(); name != want[0] || version != want[1] {
			t.Errorf("%q: got %q %q, want %q", content, name, version, want)
		}
	}
}

// Guest time is already in user/nice and must not count twice; an iowait that
// went down makes the sample invalid instead of wrapping around.
func TestCPUCounters(t *testing.T) {
	h := fakeHost(t, map[string]string{"/proc/stat": "cpu  50 0 0 50 0 0 0 0 50 0\n"})
	if total, idle, ok := h.cpuTimes(); !ok || total != 100 || idle != 50 {
		t.Errorf("cpu times = %d %d %v, want 100 50", total, idle, ok)
	}
	stat := filepath.Join(h.Root, "proc/stat")
	_ = os.WriteFile(stat, []byte("cpu  100 0 0 700 100 0 0 0 0 0\n"), 0o644)
	go func() {
		time.Sleep(20 * time.Millisecond)
		_ = os.WriteFile(stat, []byte("cpu  110 0 0 700 90 0 0 0 0 0\n"), 0o644)
	}()
	if _, ok := h.CPUPercent(context.Background(), 100*time.Millisecond); ok {
		t.Error("a sample with a falling iowait was used")
	}
}

func TestPCInventory(t *testing.T) {
	h := fakeHost(t, map[string]string{
		"/etc/os-release":                  "NAME=Ubuntu\nVERSION=\"24.04 LTS\"\n",
		"/proc/cpuinfo":                    "processor : 0\nmodel name : Intel(R) N100\nprocessor : 1\nmodel name : Intel(R) N100\n",
		"/sys/class/dmi/id/product_name":   "To be filled by O.E.M.",
		"/sys/class/dmi/id/board_name":     "NUC12WSBi5",
		"/sys/class/dmi/id/product_serial": "System Serial Number",
		"/sys/class/dmi/id/board_serial":   "BTWS1234",
		"/sys/class/net/enp1s0/address":    "aa:bb:cc:00:00:01",
		"/sys/class/net/wg0/address":       "aa:bb:cc:00:00:09",
	})
	if name, version := h.OSRelease(); name != "Ubuntu" || version != "24.04 LTS" {
		t.Errorf("os = %q %q", name, version)
	}
	if model, cores := h.CPU(); model != "Intel(R) N100" || cores != 2 {
		t.Errorf("cpu = %q %d", model, cores)
	}
	if got := h.HardwareModel(); got != "NUC12WSBi5" {
		t.Errorf("model = %q (placeholders skipped)", got)
	}
	if got := h.SerialNumber(); got != "BTWS1234" {
		t.Errorf("serial = %q", got)
	}
	if b := h.Board(); b.Name != "generic-linux" {
		t.Errorf("board = %s", b.Name)
	}
	if got := h.MACAddresses(); len(got) != 1 || got[0] != "aa:bb:cc:00:00:01" {
		t.Errorf("mac = %v", got)
	}
}

func TestEmptyHostKnowsNothingAndDoesNotFail(t *testing.T) {
	h := fakeHost(t, nil)
	if name, version := h.OSRelease(); name != "" || version != "" {
		t.Error("os from nothing")
	}
	if model, cores := h.CPU(); model != "" || cores < 1 {
		t.Errorf("cpu = %q %d (cores fall back to the runtime's count)", model, cores)
	}
	if h.SerialNumber() != "" || h.HardwareModel() != "" || len(h.MACAddresses()) != 0 || h.LocalIPv4() != "" || h.MemoryMB() != 0 {
		t.Error("facts from nothing")
	}
	if _, _, ok := h.Memory(); ok {
		t.Error("memory from nothing")
	}
	if _, _, ok := h.Disk("/"); ok {
		t.Error("disk from nothing")
	}
	if _, ok := h.LoadAverage(); ok {
		t.Error("load from nothing")
	}
	if _, ok := h.Uptime(); ok {
		t.Error("uptime from nothing")
	}
	if _, ok := h.TemperatureC(); ok {
		t.Error("temperature from nothing")
	}
	if _, ok := h.CPUPercent(context.Background(), time.Millisecond); ok {
		t.Error("cpu percent from nothing")
	}
}

func TestMetricsReadings(t *testing.T) {
	h := fakeHost(t, map[string]string{
		"/proc/meminfo":                         "MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 250 kB\n",
		"/proc/loadavg":                         "0.52 0.48 0.40 1/123 4567\n",
		"/proc/uptime":                          "12345.67 9999.00\n",
		"/proc/stat":                            "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4\n",
		"/sys/class/thermal/thermal_zone0/temp": "47500\n",
	})
	h.Statfs = func(string) (uint64, uint64, uint64, bool) { return 1000, 250, 200, true }
	if used, total, ok := h.Memory(); !ok || used != 750*1024 || total != 1000*1024 {
		t.Errorf("memory = %d %d %v", used, total, ok)
	}
	if used, total, ok := h.Disk("/"); !ok || used != 750 || total != 1000 { // free 250, available 200
		t.Errorf("disk = %d %d %v", used, total, ok)
	}
	if load, ok := h.LoadAverage(); !ok || load != [3]float64{0.52, 0.48, 0.40} {
		t.Errorf("load = %v", load)
	}
	if up, ok := h.Uptime(); !ok || up != 12345 {
		t.Errorf("uptime = %d", up)
	}
	if temp, ok := h.TemperatureC(); !ok || temp != 47.5 {
		t.Errorf("temperature = %v (thermal_zone0 is the last resort)", temp)
	}
	total, idle, ok := h.cpuTimes()
	if !ok || total != 1000 || idle != 800 {
		t.Errorf("cpu times = %d %d", total, idle)
	}
	// The same /proc/stat twice: no time passed, no reading.
	if _, ok := h.CPUPercent(context.Background(), time.Millisecond); ok {
		t.Error("cpu percent without progress")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, ok := h.CPUPercent(ctx, time.Hour); ok {
		t.Error("cpu percent after cancel")
	}
}

func TestTools(t *testing.T) {
	h := fakeHost(t, map[string]string{"/run/systemd/system/.keep": "", "/usr/sbin/sshd": ""})
	installed := map[string]bool{"systemctl": true, "wg": true, "wg-quick": true, "docker": true}
	h.LookPath = func(name string) (string, error) {
		if installed[name] {
			return "/usr/bin/" + name, nil
		}
		return "", errors.New("not found")
	}
	var ran []string
	h.Run = func(_ context.Context, name string, args ...string) bool {
		ran = append(ran, name+" "+strings.Join(args, " "))
		return true
	}
	got := h.Tools(context.Background())
	want := Tools{Systemd: true, Docker: true, DockerCompose: true, WireGuard: true, SSH: true}
	if got != want {
		t.Errorf("tools = %+v", got)
	}
	if len(ran) != 1 || ran[0] != "/usr/bin/docker compose version" {
		t.Errorf("ran %v", ran)
	}

	bare := fakeHost(t, nil)
	if got := bare.Tools(context.Background()); got != (Tools{}) {
		t.Errorf("bare tools = %+v", got)
	}
	// Docker outside PATH (snap) is still found.
	snap := fakeHost(t, nil)
	p := filepath.Join(snap.Root, "snap/bin/docker")
	_ = os.MkdirAll(filepath.Dir(p), 0o755)
	_ = os.WriteFile(p, []byte("#!/bin/sh\n"), 0o755)
	if got := snap.DockerBinary(); got != p {
		t.Errorf("docker binary = %q", got)
	}
}

func TestSystemHostReadsThisMachine(t *testing.T) {
	h := System()
	if release, machine := h.Uname(); release == "" || machine == "" {
		t.Errorf("uname = %q %q", release, machine)
	}
	if total, _, _, ok := h.Statfs("/"); !ok || total == 0 {
		t.Error("statfs / failed")
	}
	_ = h.IPv4("lo")
	_ = h.Run(context.Background(), "true")
	if h.Board() == nil {
		t.Error("no board")
	}
}
