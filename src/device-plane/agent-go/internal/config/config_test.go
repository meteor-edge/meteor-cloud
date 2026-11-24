package config

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDeriveAPIBase(t *testing.T) {
	tests := []struct {
		in        string
		forceHTTP bool
		want      string
	}{
		{"meteorxx.com", false, "https://meteorxx.com"},
		{"https://meteorxx.com/", false, "https://meteorxx.com"},
		{"http://meteorxx.com", false, "http://meteorxx.com"},
		{"www.meteorxx.com", false, "https://meteorxx.com"},
		{"meteorxx.com", true, "http://meteorxx.com"},
		{"192.168.0.107:8000", false, "http://192.168.0.107:8000"},
		{"https://api.192.168.0.107", false, "http://192.168.0.107"},
		{"localhost:8000", false, "http://localhost:8000"},
		{"[::1]:8000", false, "http://[::1]:8000"},
		{"api.meteorxx.com", false, "https://api.meteorxx.com"},
	}
	for _, tt := range tests {
		got, err := DeriveAPIBase(tt.in, tt.forceHTTP)
		if err != nil || got != tt.want {
			t.Errorf("DeriveAPIBase(%q, %v) = %q, %v; want %q", tt.in, tt.forceHTTP, got, err, tt.want)
		}
	}
	if _, err := DeriveAPIBase("  ", false); err == nil {
		t.Error("empty domain: want error")
	}
}

func TestLANHostMissingPort(t *testing.T) {
	tests := map[string]bool{
		"192.168.0.107":      true,
		"http://10.0.0.2":    true,
		"10.0.0.2:8000":      false,
		"meteorxx.com":       false,
		"http://localhost":   true,
		"http://[::1]:8000/": false,
	}
	for in, want := range tests {
		if got := LANHostMissingPort(in); got != want {
			t.Errorf("LANHostMissingPort(%q) = %v, want %v", in, got, want)
		}
	}
}

func TestConfigSaveKeepsUnknownKeys(t *testing.T) {
	path := filepath.Join(t.TempDir(), "etc", "config.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, path, `{"version":1,"domain":"a.example","future_key":{"x":1}}`)
	c, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.SetServer("b.example", "", false); err != nil {
		t.Fatal(err)
	}
	if err := c.Save(path, Owner{}); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	readJSON(t, path, &got)
	if got["domain"] != "b.example" || got["server_url"] != "https://b.example" {
		t.Errorf("saved %v", got)
	}
	if _, ok := got["future_key"]; !ok {
		t.Error("unknown key was dropped")
	}
	if st, _ := os.Stat(path); st.Mode().Perm() != 0o640 {
		t.Errorf("mode %v, want 0640", st.Mode().Perm())
	}
}

func TestLoadQuarantinesDamagedFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	writeFile(t, path, `{"domain": `)
	_, err := Load(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("err = %v, want ErrCorrupt", err)
	}
	if _, err := os.Stat(path); !errors.Is(err, fs.ErrNotExist) {
		t.Error("damaged file still in place")
	}
	matches, _ := filepath.Glob(path + ".corrupt-*")
	if len(matches) != 1 {
		t.Errorf("quarantined files: %v", matches)
	}
}

func TestSetServer(t *testing.T) {
	tests := []struct {
		name                string
		start               Config
		domain, apiBase     string
		http                bool
		wantServer          string
		wantChanged         bool
		wantAPIBaseNotEmpty bool
	}{
		{name: "first domain", domain: "a.example", wantServer: "https://a.example"},
		{name: "api base wins", domain: "a.example", apiBase: "http://10.0.0.1:8000", wantServer: "http://10.0.0.1:8000", wantAPIBaseNotEmpty: true},
		{name: "http flag", domain: "a.example", http: true, wantServer: "http://a.example", wantAPIBaseNotEmpty: true},
		{name: "server change", start: Config{Domain: "a.example"}, domain: "b.example", wantServer: "https://b.example", wantChanged: true},
		{name: "same server", start: Config{Domain: "a.example"}, domain: "https://a.example", wantServer: "https://a.example"},
		{name: "new domain clears api base", start: Config{APIBase: "http://10.0.0.1"}, domain: "a.example", wantServer: "https://a.example", wantChanged: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := tt.start
			changed, err := c.SetServer(tt.domain, tt.apiBase, tt.http)
			if err != nil {
				t.Fatal(err)
			}
			if c.Server() != tt.wantServer || changed != tt.wantChanged || (c.APIBase != "") != tt.wantAPIBaseNotEmpty {
				t.Errorf("server %q changed %v api_base %q", c.Server(), changed, c.APIBase)
			}
		})
	}
}

func TestDeviceHeartbeat(t *testing.T) {
	tests := map[int]int{0: 60, 1: 5, 30: 30}
	for in, want := range tests {
		d := &Device{HeartbeatIntervalSeconds: in}
		if got := d.Heartbeat().Seconds(); int(got) != want {
			t.Errorf("Heartbeat(%d) = %v, want %d", in, got, want)
		}
	}
	var nilDevice *Device
	if nilDevice.Heartbeat().Seconds() != 60 {
		t.Error("nil device should use the default")
	}
}

func TestDropEnrollment(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	if err := os.MkdirAll(p.IdentityDir, 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, p.DeviceTokenFile(), "tok")
	writeFile(t, p.APIKeyFile(), "key")
	writeFile(t, filepath.Join(p.WireGuardDir()+"-keep"), "x")
	removed, err := DropEnrollment(p)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(removed, ",") != "device-token,api-key" {
		t.Errorf("removed %v", removed)
	}
}

func TestWriteFileAtomicLeavesNoTempFiles(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "f")
	for _, body := range []string{"one", "two"} {
		if err := WriteFileAtomic(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("entries %v", entries)
	}
	if b, _ := os.ReadFile(path); string(b) != "two" {
		t.Errorf("content %q", b)
	}
	if err := WriteFileAtomic(filepath.Join(dir, "missing", "f"), nil, 0o600); err == nil {
		t.Error("want error for a missing directory")
	}
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func readJSON(t *testing.T, path string, v any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, v); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
}
