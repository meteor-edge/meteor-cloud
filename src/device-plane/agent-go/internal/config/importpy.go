package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"
)

// pythonStaging is the journal the Python agent used so the token and MQTT files
// changed together. A journal with a DONE file was complete and wins.
const pythonStaging = ".device-secrets-staging"

// Keys the Python config.json held that now live in identity/device.json.
var pythonDeviceKeys = []string{"device_id", "organization_id", "name", "heartbeat_interval_seconds"}

// ImportOptions tunes ImportPython.
type ImportOptions struct {
	// Force overwrites files that already exist in the new layout.
	Force bool
	// Owner hands the new files to the meteor user when running as root.
	Owner Owner
	// Now is the clock for quarantine names; zero means time.Now.
	Now func() time.Time
}

// ImportReport says what ImportPython did, for the CLI to print.
type ImportReport struct {
	From     string
	Imported []string
	Skipped  []string
	Removed  []string
	Backup   string
}

// LooksLikePython reports whether dir holds a Python meteorcli install that has
// not been imported: a config.json without a version field, or a device token.
func LooksLikePython(dir string) bool {
	if fileExists(filepath.Join(dir, "device-token")) {
		return true
	}
	var probe struct {
		Version *int `json:"version"`
	}
	if _, err := ReadJSON(filepath.Join(dir, "config.json"), &probe); err != nil {
		return false
	}
	return probe.Version == nil
}

// ImportPython moves a Python install in dir (normally /etc/meteorcli) into the
// new layout: settings stay in config.json, enrollment facts go to device.json,
// secrets go to the identity directory, signing pins and applied software go to
// the state directory. Secrets are removed from dir only after every new file
// was written. Running it again does nothing.
func ImportPython(from string, p Paths, opt ImportOptions) (ImportReport, error) {
	now := opt.Now
	if now == nil {
		now = time.Now
	}
	rep := ImportReport{From: from}
	if err := opt.Owner.EnsureDir(p.IdentityDir, 0o700); err != nil {
		return rep, err
	}
	if err := opt.Owner.EnsureDir(p.StateDir, 0o700); err != nil {
		return rep, err
	}

	// A finished staging journal holds newer secrets than the files beside it.
	src := func(name string) string {
		staged := filepath.Join(from, pythonStaging, name)
		if fileExists(filepath.Join(from, pythonStaging, "DONE")) && fileExists(staged) {
			return staged
		}
		return filepath.Join(from, name)
	}

	legacyConfig := filepath.Join(from, "config.json")
	sameFile := samePath(legacyConfig, p.ConfigFile)
	if err := importConfig(legacyConfig, p, sameFile, opt, now, &rep); err != nil {
		return rep, err
	}

	type move struct{ name, src, dst string }
	secrets := []move{
		{"device-token", src("device-token"), p.DeviceTokenFile()},
		{"api-key", filepath.Join(from, "api-key"), p.APIKeyFile()},
		{"mqtt-ca.crt", src("mqtt-ca.crt"), p.MQTTCAFile()},
		{"wireguard/private.key", filepath.Join(from, "wireguard", "private.key"), filepath.Join(p.WireGuardDir(), "private.key")},
		{"wireguard/public.key", filepath.Join(from, "wireguard", "public.key"), filepath.Join(p.WireGuardDir(), "public.key")},
	}
	var done []string
	for _, m := range secrets {
		ok, err := copyIfNeeded(m.src, m.dst, opt, &rep, m.name)
		if err != nil {
			return rep, err
		}
		if ok {
			done = append(done, m.src)
		}
	}

	// JSON files gain a version field; unknown keys are kept.
	jsonMoves := []struct {
		name, src, dst string
		fix            func(Object)
	}{
		{"mqtt.json", src("mqtt.json"), p.MQTTFile(), func(o Object) {
			if v, ok := o["ca_cert_path"]; ok && string(v) != "null" {
				o["ca_cert_path"] = mustJSON(p.MQTTCAFile())
			}
		}},
		{"claim-secret", filepath.Join(from, "claim-secret"), p.PendingClaimFile(), nil},
		{"signing-keys.json", filepath.Join(from, "signing-keys.json"), filepath.Join(p.StateDir, "signing-keys.json"), nil},
		{"applied-software.json", filepath.Join(from, "applied-software.json"), filepath.Join(p.StateDir, "applied-software.json"), nil},
	}
	for _, m := range jsonMoves {
		ok, err := importJSONFile(m.src, m.dst, m.fix, opt, now, &rep, m.name)
		if err != nil {
			return rep, err
		}
		if ok {
			done = append(done, m.src)
		}
	}

	// Everything is written: remove the old copies so there is one source of truth.
	for _, f := range done {
		if err := os.Remove(f); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return rep, fmt.Errorf("import: remove %s: %w", f, err)
		}
		rel, _ := filepath.Rel(from, f)
		rep.Removed = append(rep.Removed, rel)
	}
	if err := os.RemoveAll(filepath.Join(from, pythonStaging)); err != nil {
		return rep, fmt.Errorf("import: remove staging: %w", err)
	}
	_ = os.Remove(filepath.Join(from, "wireguard")) // only if empty
	return rep, nil
}

// importConfig converts the Python config.json: settings stay, device facts move.
func importConfig(legacy string, p Paths, sameFile bool, opt ImportOptions, now func() time.Time, rep *ImportReport) error {
	var old struct {
		Version                  *int   `json:"version"`
		Domain                   string `json:"domain"`
		APIBase                  string `json:"api_base"`
		ServerURL                string `json:"server_url"`
		DeviceID                 string `json:"device_id"`
		OrganizationID           string `json:"organization_id"`
		Name                     string `json:"name"`
		HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
	}
	raw, err := ReadJSON(legacy, &old)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return nil
	case errors.Is(err, ErrCorrupt):
		dst, qerr := Quarantine(legacy, now())
		if qerr != nil {
			return fmt.Errorf("import: %w", qerr)
		}
		rep.Skipped = append(rep.Skipped, "config.json (damaged, moved to "+filepath.Base(dst)+")")
		return nil
	case err != nil:
		return fmt.Errorf("import: %w", err)
	}
	if old.Version != nil {
		return nil // already the new format
	}

	if old.DeviceID != "" {
		if fileExists(p.DeviceFile()) && !opt.Force {
			rep.Skipped = append(rep.Skipped, "device.json (exists)")
		} else {
			d := &Device{
				DeviceID: old.DeviceID, OrganizationID: old.OrganizationID,
				Name: old.Name, HeartbeatIntervalSeconds: old.HeartbeatIntervalSeconds,
			}
			if err := d.Save(p.DeviceFile(), opt.Owner); err != nil {
				return fmt.Errorf("import: %w", err)
			}
			rep.Imported = append(rep.Imported, "device.json")
		}
	}

	if !sameFile && fileExists(p.ConfigFile) && !opt.Force {
		rep.Skipped = append(rep.Skipped, "config.json (exists)")
		return nil
	}
	if sameFile {
		backup := legacy + ".python"
		data, err := os.ReadFile(legacy)
		if err != nil {
			return fmt.Errorf("import: %w", err)
		}
		if err := WriteFileAtomic(backup, data, 0o600); err != nil {
			return fmt.Errorf("import: %w", err)
		}
		rep.Backup = backup
	}
	for _, k := range append([]string{"version"}, pythonDeviceKeys...) {
		delete(raw, k)
	}
	c := &Config{Domain: old.Domain, APIBase: old.APIBase, ServerURL: old.ServerURL, extra: raw}
	if err := c.Save(p.ConfigFile, opt.Owner); err != nil {
		return fmt.Errorf("import: %w", err)
	}
	rep.Imported = append(rep.Imported, "config.json")
	return nil
}

// copyIfNeeded copies a secret file (0600, agent-owned). It returns true when src
// may be removed: it was copied, or the same content is already in place.
func copyIfNeeded(src, dst string, opt ImportOptions, rep *ImportReport, name string) (bool, error) {
	data, err := os.ReadFile(src)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("import %s: %w", name, err)
	}
	if fileExists(dst) && !opt.Force {
		rep.Skipped = append(rep.Skipped, name+" (exists)")
		return false, nil
	}
	if err := opt.Owner.EnsureDir(filepath.Dir(dst), 0o700); err != nil {
		return false, err
	}
	if err := WriteFileAtomic(dst, data, 0o600); err != nil {
		return false, fmt.Errorf("import %s: %w", name, err)
	}
	if err := opt.Owner.Agent(dst); err != nil {
		return false, err
	}
	rep.Imported = append(rep.Imported, name)
	return true, nil
}

// importJSONFile copies a JSON object, adds "version": 1 when it has none, and
// lets fix adjust it. A damaged source is quarantined and skipped.
func importJSONFile(src, dst string, fix func(Object), opt ImportOptions, now func() time.Time, rep *ImportReport, name string) (bool, error) {
	var obj Object
	_, err := ReadJSON(src, &obj)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return false, nil
	case errors.Is(err, ErrCorrupt):
		if q, qerr := Quarantine(src, now()); qerr == nil {
			rep.Skipped = append(rep.Skipped, name+" (damaged, moved to "+filepath.Base(q)+")")
			return false, nil
		}
		return false, fmt.Errorf("import %s: %w", name, err)
	case err != nil:
		return false, fmt.Errorf("import %s: %w", name, err)
	}
	if fileExists(dst) && !opt.Force {
		rep.Skipped = append(rep.Skipped, name+" (exists)")
		return false, nil
	}
	if _, ok := obj["version"]; !ok {
		obj["version"] = json.RawMessage("1")
	}
	if fix != nil {
		fix(obj)
	}
	if err := WriteJSON(dst, struct{}{}, obj, 0o600); err != nil {
		return false, fmt.Errorf("import %s: %w", name, err)
	}
	if err := opt.Owner.Agent(dst); err != nil {
		return false, err
	}
	rep.Imported = append(rep.Imported, name)
	return true, nil
}

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Mode().IsRegular()
}

func samePath(a, b string) bool {
	ca, err1 := filepath.Abs(a)
	cb, err2 := filepath.Abs(b)
	return err1 == nil && err2 == nil && ca == cb
}

func mustJSON(s string) json.RawMessage {
	b, _ := json.Marshal(s)
	return b
}
