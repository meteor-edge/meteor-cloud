package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadEngine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "engine.json")
	if e, err := LoadEngine(path); err != nil || e.Name != "docker" || e.Endpoint != "" {
		t.Fatalf("no file: %+v, %v", e, err)
	}
	for content, want := range map[string]string{
		`{"name":"podman"}`: "/run/podman/podman.sock",
		`{"name":"kiri","endpoint":"unix:///run/kiri.sock"}`: "/run/kiri.sock",
		`{"name":"docker"}`: "",
	} {
		_ = os.WriteFile(path, []byte(content), 0o600)
		if e, err := LoadEngine(path); err != nil || e.Socket() != want {
			t.Errorf("%s: %+v, %v", content, e, err)
		}
	}
	for _, bad := range []string{
		`{}`, `x`, `{"name":"kiri"}`, `{"name":"a b","endpoint":"unix:///x"}`,
		`{"name":"x","endpoint":"tcp://10.0.0.1:2375"}`, `{"name":"x","endpoint":"unix:///a/../b"}`,
	} {
		_ = os.WriteFile(path, []byte(bad), 0o600)
		if e, err := LoadEngine(path); err == nil {
			t.Errorf("%s gave %+v", bad, e)
		}
	}
	_ = os.WriteFile(path, []byte(`{"name":"podman"}`), 0o600)
	_ = os.Chmod(path, 0o666)
	if _, err := LoadEngine(path); err == nil {
		t.Error("a file anyone can write was accepted")
	}
}
