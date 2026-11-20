package config

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// pythonInstall writes what a Python meteorcli left in /etc/meteorcli after
// enrolling, with MQTT and WireGuard set up.
func pythonInstall(t *testing.T, dir string) {
	t.Helper()
	writeFile(t, filepath.Join(dir, "config.json"), `{
  "server_url": "https://meteor.example",
  "device_id": "4f1c2a8e-0000-4000-8000-000000000001",
  "organization_id": "4f1c2a8e-0000-4000-8000-000000000002",
  "name": "edge-01",
  "heartbeat_interval_seconds": 30,
  "domain": "meteor.example",
  "api_base": null,
  "added_by_hand": "keep me"
}`)
	writeFile(t, filepath.Join(dir, "device-token"), "dev_secret_token\n")
	writeFile(t, filepath.Join(dir, "api-key"), "key_secret")
	writeFile(t, filepath.Join(dir, "mqtt.json"),
		`{"host":"mqtt.example","port":8883,"username":"device-x","tls":true,"password":"mqtt_pw","ca_cert_path":"`+dir+`/mqtt-ca.crt","config_version":"7"}`)
	writeFile(t, filepath.Join(dir, "mqtt-ca.crt"), "-----BEGIN CERTIFICATE-----\n")
	writeFile(t, filepath.Join(dir, "signing-keys.json"), `{"pinned":{"k1":"abc"}}`)
	writeFile(t, filepath.Join(dir, "wireguard", "private.key"), "wg_private")
	writeFile(t, filepath.Join(dir, "wireguard", "public.key"), "wg_public")
}

func TestImportPythonInPlace(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	if !LooksLikePython(p.LegacyDir) {
		t.Fatal("LooksLikePython = false for a Python install")
	}

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"config.json", "device.json", "device-token", "api-key", "mqtt.json", "mqtt-ca.crt", "signing-keys.json", "wireguard/private.key"} {
		if !slices.Contains(rep.Imported, want) {
			t.Errorf("Imported %v misses %s", rep.Imported, want)
		}
	}

	c, err := Load(p.ConfigFile)
	if err != nil {
		t.Fatal(err)
	}
	if c.Version != Version || c.Server() != "https://meteor.example" {
		t.Errorf("config %+v", c)
	}
	raw, _ := os.ReadFile(p.ConfigFile)
	if strings.Contains(string(raw), "device_id") || !strings.Contains(string(raw), "added_by_hand") {
		t.Errorf("config.json = %s", raw)
	}

	d, err := LoadDevice(p.DeviceFile())
	if err != nil {
		t.Fatal(err)
	}
	if d.DeviceID != "4f1c2a8e-0000-4000-8000-000000000001" || d.Name != "edge-01" || d.HeartbeatIntervalSeconds != 30 {
		t.Errorf("device %+v", d)
	}

	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "dev_secret_token\n" {
		t.Errorf("token %q", b)
	}
	if st, _ := os.Stat(p.DeviceTokenFile()); st.Mode().Perm() != 0o600 {
		t.Errorf("token mode %v", st.Mode().Perm())
	}
	if st, _ := os.Stat(p.IdentityDir); st.Mode().Perm() != 0o700 {
		t.Errorf("identity dir mode %v", st.Mode().Perm())
	}
	var mqtt map[string]any
	readJSON(t, p.MQTTFile(), &mqtt)
	if mqtt["ca_cert_path"] != p.MQTTCAFile() || mqtt["version"] != float64(1) || mqtt["config_version"] != "7" {
		t.Errorf("mqtt.json %v", mqtt)
	}
	if _, err := os.Stat(filepath.Join(p.StateDir, "signing-keys.json")); err != nil {
		t.Error(err)
	}

	// Old secrets are gone, the old config is kept as a backup.
	for _, gone := range []string{"device-token", "api-key", "mqtt.json", "wireguard"} {
		if _, err := os.Stat(filepath.Join(p.LegacyDir, gone)); err == nil {
			t.Errorf("%s still in %s", gone, p.LegacyDir)
		}
	}
	if rep.Backup == "" {
		t.Error("no backup of the Python config")
	}
	if LooksLikePython(p.LegacyDir) {
		t.Error("LooksLikePython = true after import")
	}

	// A second run finds nothing to do.
	again, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil || len(again.Imported) != 0 {
		t.Errorf("second import: %+v, %v", again, err)
	}
}

func TestImportPythonPrefersFinishedStaging(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	staging := filepath.Join(p.LegacyDir, pythonStaging)
	writeFile(t, filepath.Join(staging, "device-token"), "rotated_token")
	writeFile(t, filepath.Join(staging, "DONE"), `{"token_file":"device-token"}`)

	if _, err := ImportPython(p.LegacyDir, p, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "rotated_token" {
		t.Errorf("token %q, want the staged one", b)
	}
	if _, err := os.Stat(staging); err == nil {
		t.Error("staging left behind")
	}
}

func TestImportPythonIgnoresUnfinishedStaging(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	writeFile(t, filepath.Join(p.LegacyDir, pythonStaging, "device-token"), "half_written")

	if _, err := ImportPython(p.LegacyDir, p, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "dev_secret_token\n" {
		t.Errorf("token %q", b)
	}
}

func TestImportPythonFromOtherDirKeepsExisting(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	old := filepath.Join(t.TempDir(), "home-config")
	pythonInstall(t, old)
	writeFile(t, p.ConfigFile, `{"version":1,"domain":"meteor.example"}`)
	writeFile(t, p.DeviceTokenFile(), "already_here")

	rep, err := ImportPython(old, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(rep.Skipped, "config.json (exists)") || !slices.Contains(rep.Skipped, "device-token (exists)") {
		t.Errorf("skipped %v", rep.Skipped)
	}
	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "already_here" {
		t.Errorf("token overwritten: %q", b)
	}
	// A secret that was not copied stays where it was.
	if _, err := os.Stat(filepath.Join(old, "device-token")); err != nil {
		t.Error("skipped secret was removed")
	}

	rep, err = ImportPython(old, p, ImportOptions{Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "dev_secret_token\n" || len(rep.Skipped) != 0 {
		t.Errorf("force: token %q skipped %v", b, rep.Skipped)
	}
}

func TestImportPythonDamagedConfig(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	writeFile(t, filepath.Join(p.LegacyDir, "config.json"), `{"server_url": `)
	writeFile(t, filepath.Join(p.LegacyDir, "device-token"), "tok")
	now := func() time.Time { return time.Unix(1700000000, 0) }

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{Now: now})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(rep.Skipped, "config.json (damaged, moved to config.json.corrupt-1700000000)") {
		t.Errorf("skipped %v", rep.Skipped)
	}
	if !slices.Contains(rep.Imported, "device-token") {
		t.Errorf("imported %v", rep.Imported)
	}
}

func TestImportPythonRefusesAnotherServer(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	old := filepath.Join(t.TempDir(), "home-config")
	pythonInstall(t, old)
	writeFile(t, p.ConfigFile, `{"version":1,"domain":"other.example"}`)

	if _, err := ImportPython(old, p, ImportOptions{}); err == nil || !strings.Contains(err.Error(), "--force") {
		t.Fatalf("err = %v, want a server conflict", err)
	}
	if _, err := os.Stat(p.DeviceTokenFile()); err == nil {
		t.Error("enrollment files written despite the conflict")
	}
	if _, err := ImportPython(old, p, ImportOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	if c, _ := Load(p.ConfigFile); c.Server() != "https://meteor.example" {
		t.Errorf("force kept server %q", c.Server())
	}
}

func TestImportPythonStagingSkippedIsKept(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	staging := filepath.Join(p.LegacyDir, pythonStaging)
	writeFile(t, filepath.Join(staging, "device-token"), "rotated_token")
	writeFile(t, filepath.Join(staging, "DONE"), `{}`)
	writeFile(t, p.DeviceTokenFile(), "already_here")

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(rep.Skipped, "device-token (exists)") {
		t.Errorf("skipped %v", rep.Skipped)
	}
	if _, err := os.Stat(filepath.Join(staging, "device-token")); err != nil {
		t.Fatal("skipped staged token was deleted")
	}
	if _, err := ImportPython(p.LegacyDir, p, ImportOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p.DeviceTokenFile()); string(b) != "rotated_token" {
		t.Errorf("force imported %q, want the staged token", b)
	}
	if _, err := os.Stat(staging); err == nil {
		t.Error("staging kept after everything was imported")
	}
	if LooksLikePython(p.LegacyDir) {
		t.Error("plain token left behind")
	}
}

func TestImportPythonInPlaceDeviceConflictKeepsPythonConfig(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	writeFile(t, p.DeviceFile(), `{"version":1,"device_id":"someone-else"}`)

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(rep.Imported, "config.json") {
		t.Fatalf("config converted despite the device.json conflict: %+v", rep)
	}
	if _, err := ImportPython(p.LegacyDir, p, ImportOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	d, _ := LoadDevice(p.DeviceFile())
	if d.DeviceID != "4f1c2a8e-0000-4000-8000-000000000001" {
		t.Errorf("device %+v", d)
	}
}

func TestImportPythonKeepsCAFileAndAcceptsIdenticalSecrets(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	writeFile(t, filepath.Join(p.LegacyDir, "config.json"), `{"server_url":"https://meteor.example","ca_file":"/etc/ssl/meteor-ca.pem"}`)
	writeFile(t, p.APIKeyFile(), "key_secret") // same as the Python copy

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := Load(p.ConfigFile); c.CAFile != "/etc/ssl/meteor-ca.pem" {
		t.Errorf("ca_file %q", c.CAFile)
	}
	if slices.Contains(rep.Skipped, "api-key (exists)") || !slices.Contains(rep.Removed, "api-key") {
		t.Errorf("identical api-key: %+v", rep)
	}
}

func TestEnsureDirMakesParentsTraversable(t *testing.T) {
	base := t.TempDir()
	dir := filepath.Join(base, "var", "lib", "meteorcli", "identity")
	if err := (Owner{}).EnsureDir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(filepath.Dir(dir)); st.Mode().Perm() != 0o755 {
		t.Errorf("parent mode %v", st.Mode().Perm())
	}
	if st, _ := os.Stat(dir); st.Mode().Perm() != 0o700 {
		t.Errorf("dir mode %v", st.Mode().Perm())
	}
}

func TestImportPythonSameDeviceOtherFieldsKeepsPythonConfig(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	pythonInstall(t, p.LegacyDir)
	// Same device id, but the name and heartbeat differ from the Python config.
	writeFile(t, p.DeviceFile(), `{"version":1,"device_id":"4f1c2a8e-0000-4000-8000-000000000001",
 "organization_id":"4f1c2a8e-0000-4000-8000-000000000002","name":"old-name","heartbeat_interval_seconds":60}`)

	rep, err := ImportPython(p.LegacyDir, p, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(rep.Imported, "config.json") {
		t.Fatalf("config converted although device.json differs: %+v", rep)
	}
	if _, err := ImportPython(p.LegacyDir, p, ImportOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	d, _ := LoadDevice(p.DeviceFile())
	if d.Name != "edge-01" || d.HeartbeatIntervalSeconds != 30 {
		t.Errorf("force did not import the Python facts: %+v", d)
	}
}
