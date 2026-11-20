package config

import (
	"bytes"
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

	legacyConfig := filepath.Join(from, "config.json")
	sameFile := samePath(legacyConfig, p.ConfigFile)
	old, raw, err := readPythonConfig(legacyConfig, now, &rep)
	if err != nil {
		return rep, err
	}
	// Enrollment files belong to one server. Refuse to mix two servers before
	// anything is written.
	if !sameFile && !opt.Force {
		if err := checkSameServer(old, p.ConfigFile); err != nil {
			return rep, err
		}
	}

	if err := opt.Owner.EnsureDir(p.IdentityDir, 0o700); err != nil {
		return rep, err
	}
	if err := opt.Owner.EnsureDir(p.StateDir, 0o700); err != nil {
		return rep, err
	}

	deviceOK, err := importDevice(old, p, opt, &rep)
	if err != nil {
		return rep, err
	}
	if err := importConfig(old, raw, legacyConfig, p, sameFile, deviceOK, opt, &rep); err != nil {
		return rep, err
	}

	// A finished staging journal holds newer secrets than the files beside it.
	finished := fileExists(filepath.Join(from, pythonStaging, "DONE"))
	src := func(name string) (path, superseded string) {
		staged := filepath.Join(from, pythonStaging, name)
		plain := filepath.Join(from, name)
		if finished && fileExists(staged) {
			return staged, plain
		}
		return plain, ""
	}
	var done []string
	record := func(ok bool, path, superseded string) {
		if ok {
			done = append(done, path)
			if superseded != "" {
				done = append(done, superseded) // the older plain copy goes too
			}
		}
	}

	type move struct{ name, src, superseded, dst string }
	staged := func(name, dst string) move {
		path, old := src(name)
		return move{name, path, old, dst}
	}
	plain := func(name, rel, dst string) move { return move{name, filepath.Join(from, rel), "", dst} }
	secrets := []move{
		staged("device-token", p.DeviceTokenFile()),
		plain("api-key", "api-key", p.APIKeyFile()),
		staged("mqtt-ca.crt", p.MQTTCAFile()),
		plain("wireguard/private.key", filepath.Join("wireguard", "private.key"), filepath.Join(p.WireGuardDir(), "private.key")),
		plain("wireguard/public.key", filepath.Join("wireguard", "public.key"), filepath.Join(p.WireGuardDir(), "public.key")),
	}
	for _, m := range secrets {
		ok, err := copyIfNeeded(m.src, m.dst, opt, &rep, m.name)
		if err != nil {
			return rep, err
		}
		record(ok, m.src, m.superseded)
	}

	// JSON files gain a version field; unknown keys are kept.
	fixMQTT := func(o Object) {
		if v, ok := o["ca_cert_path"]; ok && string(v) != "null" {
			o["ca_cert_path"] = mustJSON(p.MQTTCAFile())
		}
	}
	jsonMoves := []struct {
		move
		fix func(Object)
	}{
		{staged("mqtt.json", p.MQTTFile()), fixMQTT},
		{plain("claim-secret", "claim-secret", p.PendingClaimFile()), nil},
		{plain("signing-keys.json", "signing-keys.json", filepath.Join(p.StateDir, "signing-keys.json")), nil},
		{plain("applied-software.json", "applied-software.json", filepath.Join(p.StateDir, "applied-software.json")), nil},
	}
	for _, m := range jsonMoves {
		ok, err := importJSONFile(m.src, m.dst, m.fix, opt, now, &rep, m.name)
		if err != nil {
			return rep, err
		}
		record(ok, m.src, m.superseded)
	}

	// Everything is written: remove the old copies so there is one source of truth.
	for _, f := range done {
		if err := os.Remove(f); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return rep, fmt.Errorf("import: remove %s: %w", f, err)
		}
		rel, _ := filepath.Rel(from, f)
		rep.Removed = append(rep.Removed, rel)
	}
	if err := cleanStaging(filepath.Join(from, pythonStaging), finished); err != nil {
		return rep, err
	}
	_ = os.Remove(filepath.Join(from, "wireguard")) // only if empty
	return rep, nil
}

// pythonConfig is the Python agent's config.json.
type pythonConfig struct {
	Version                  *int   `json:"version"`
	Domain                   string `json:"domain"`
	APIBase                  string `json:"api_base"`
	ServerURL                string `json:"server_url"`
	CAFile                   string `json:"ca_file"`
	DeviceID                 string `json:"device_id"`
	OrganizationID           string `json:"organization_id"`
	Name                     string `json:"name"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
}

func (c *pythonConfig) server() string {
	if c == nil {
		return ""
	}
	return (&Config{Domain: c.Domain, APIBase: c.APIBase, ServerURL: c.ServerURL}).Server()
}

// readPythonConfig returns nil (and no error) when there is nothing to convert:
// no file, a damaged file (quarantined), or a file already in the new format.
func readPythonConfig(path string, now func() time.Time, rep *ImportReport) (*pythonConfig, Object, error) {
	var old pythonConfig
	raw, err := ReadJSON(path, &old)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return nil, nil, nil
	case errors.Is(err, ErrCorrupt):
		dst, qerr := Quarantine(path, now())
		if qerr != nil {
			return nil, nil, fmt.Errorf("import: %w", qerr)
		}
		rep.Skipped = append(rep.Skipped, "config.json (damaged, moved to "+filepath.Base(dst)+")")
		return nil, nil, nil
	case err != nil:
		return nil, nil, fmt.Errorf("import: %w", err)
	}
	if old.Version != nil {
		return nil, nil, nil
	}
	return &old, raw, nil
}

// checkSameServer fails when the new config already points at another server.
func checkSameServer(old *pythonConfig, newConfig string) error {
	src := old.server()
	if src == "" {
		return nil
	}
	cur, err := Load(newConfig)
	if err != nil {
		return nil // missing or damaged: nothing to conflict with
	}
	if dst := cur.Server(); dst != "" && dst != src {
		return fmt.Errorf("import: %s is configured for %s but the Python install is enrolled on %s; use --force to replace", newConfig, dst, src)
	}
	return nil
}

// importDevice writes device.json from the Python config. It returns false when
// an existing device.json for another device was kept, so the caller keeps the
// Python config as the source for a --force retry.
func importDevice(old *pythonConfig, p Paths, opt ImportOptions, rep *ImportReport) (bool, error) {
	if old == nil || old.DeviceID == "" {
		return true, nil
	}
	if cur, err := LoadDevice(p.DeviceFile()); err == nil && !opt.Force {
		if cur.DeviceID == old.DeviceID && cur.OrganizationID == old.OrganizationID &&
			cur.Name == old.Name && cur.HeartbeatIntervalSeconds == old.HeartbeatIntervalSeconds {
			return true, nil // already imported
		}
		// Any difference keeps the Python config as the source for a --force retry.
		rep.Skipped = append(rep.Skipped, "device.json (exists with other values for device "+cur.DeviceID+")")
		return false, nil
	}
	d := &Device{
		DeviceID: old.DeviceID, OrganizationID: old.OrganizationID,
		Name: old.Name, HeartbeatIntervalSeconds: old.HeartbeatIntervalSeconds,
	}
	if err := d.Save(p.DeviceFile(), opt.Owner); err != nil {
		return false, fmt.Errorf("import: %w", err)
	}
	rep.Imported = append(rep.Imported, "device.json")
	return true, nil
}

// importConfig converts the Python config.json: settings stay, device facts move
// to device.json. In place, it waits until device.json was imported, because the
// rewrite drops the device facts.
func importConfig(old *pythonConfig, raw Object, legacy string, p Paths, sameFile, deviceOK bool, opt ImportOptions, rep *ImportReport) error {
	if old == nil {
		return nil
	}
	if sameFile && !deviceOK {
		rep.Skipped = append(rep.Skipped, "config.json (kept in Python format until device.json is imported)")
		return nil
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
	c := &Config{Domain: old.Domain, APIBase: old.APIBase, ServerURL: old.ServerURL, CAFile: old.CAFile, extra: raw}
	if err := c.Save(p.ConfigFile, opt.Owner); err != nil {
		return fmt.Errorf("import: %w", err)
	}
	rep.Imported = append(rep.Imported, "config.json")
	return nil
}

// cleanStaging drops an unfinished journal, and a finished one once every staged
// file was imported. A finished journal with skipped files stays for --force.
func cleanStaging(dir string, finished bool) error {
	if finished {
		entries, err := os.ReadDir(dir)
		if err != nil && !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("import: staging: %w", err)
		}
		for _, e := range entries {
			if e.Name() != "DONE" {
				return nil
			}
		}
	}
	if err := os.RemoveAll(dir); err != nil {
		return fmt.Errorf("import: remove staging: %w", err)
	}
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
		if cur, err := os.ReadFile(dst); err == nil && bytes.Equal(cur, data) {
			return true, nil // already imported: the old copy may go
		}
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
