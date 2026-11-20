package identity

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
)

// fileVersion is the format of the JSON files this package writes.
const fileVersion = 1

// Staging: the device token, MQTT settings and device.json of a new identity
// must land together, or a crash could pair a new token with an old device id.
// They are written into Paths.StagingDir with a doneFile marker, then moved into
// place. On start an unfinished staging directory is dropped and a finished one
// is moved into place (the Python agent's .device-secrets-staging did the same).
const doneFile = "DONE"

// MQTT is the broker connection stored in identity/mqtt.json. Password is a
// secret. The JSON keys are the Python agent's, so imported files work as is.
type MQTT struct {
	Version       int     `json:"version"`
	Host          string  `json:"host"`
	Port          int     `json:"port"`
	Username      string  `json:"username"`
	Password      string  `json:"password"`
	TLS           bool    `json:"tls"`
	CACertPath    *string `json:"ca_cert_path"`
	ConfigVersion *string `json:"config_version"`

	extra config.Object
}

// PendingClaim is an enrollment request waiting for approval
// (identity/pending-claim.json). ClaimSecret is a secret. Name is kept so an
// expired request can be submitted again with the same name.
type PendingClaim struct {
	Version     int    `json:"version"`
	RequestID   string `json:"request_id"`
	ClaimSecret string `json:"claim_secret"`
	Name        string `json:"name"`

	extra config.Object
}

// files reads and writes the identity directory. The agent (user meteor) owns it.
type files struct {
	p   config.Paths
	now func() time.Time
}

// readSecret returns a trimmed secret file, "" when it is missing or empty.
func (f files) readSecret(path string) (string, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("identity: %w", err)
	}
	return strings.TrimSpace(string(data)), nil
}

func (f files) writeSecret(path, value string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	if err := config.WriteFileAtomic(path, []byte(value), 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return nil
}

func (f files) remove(paths ...string) error {
	for _, p := range paths {
		if err := os.Remove(p); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("identity: %w", err)
		}
	}
	return nil
}

// readJSON loads a JSON file into v. A missing file returns (false, nil). A
// damaged one is moved to <name>.corrupt-<unix time> and also returns false:
// that part of the identity starts clean.
func (f files) readJSON(path string, v any) (config.Object, bool, error) {
	raw, err := config.ReadJSON(path, v)
	switch {
	case err == nil:
		return raw, true, nil
	case errors.Is(err, fs.ErrNotExist):
		return nil, false, nil
	case errors.Is(err, config.ErrCorrupt):
		dst, qerr := config.Quarantine(path, f.now())
		if qerr != nil {
			return nil, false, fmt.Errorf("identity: %w (and %v)", err, qerr)
		}
		return nil, false, fmt.Errorf("identity: %w (moved to %s)", err, filepath.Base(dst))
	default:
		return nil, false, fmt.Errorf("identity: %w", err)
	}
}

func (f files) loadDevice() (*config.Device, error) {
	d, err := config.LoadDevice(f.p.DeviceFile())
	switch {
	case err == nil:
		return d, nil
	case errors.Is(err, fs.ErrNotExist):
		return nil, nil
	case errors.Is(err, config.ErrCorrupt):
		dst, qerr := config.Quarantine(f.p.DeviceFile(), f.now())
		if qerr != nil {
			return nil, fmt.Errorf("identity: %w (and %v)", err, qerr)
		}
		return nil, fmt.Errorf("identity: %w (moved to %s)", err, filepath.Base(dst))
	default:
		return nil, fmt.Errorf("identity: %w", err)
	}
}

// loadMQTT returns the stored broker settings, or nil when there are none or
// they are unusable (no password, or TLS off: MQTT always uses TLS).
func (f files) loadMQTT() (*MQTT, error) {
	var m MQTT
	raw, ok, err := f.readJSON(f.p.MQTTFile(), &m)
	if !ok || m.Password == "" || !m.TLS {
		return nil, err
	}
	m.extra = raw
	if m.Host == "" {
		m.Host = "localhost"
	}
	if m.Port == 0 {
		m.Port = 8883
	}
	return &m, nil
}

func (f files) loadPending() (*PendingClaim, error) {
	var c PendingClaim
	raw, ok, err := f.readJSON(f.p.PendingClaimFile(), &c)
	if !ok || c.RequestID == "" || c.ClaimSecret == "" {
		return nil, err
	}
	c.extra = raw
	return &c, nil
}

func (f files) savePending(c *PendingClaim) error {
	c.Version = fileVersion
	if err := os.MkdirAll(f.p.IdentityDir, 0o700); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	if err := config.WriteJSON(f.p.PendingClaimFile(), c, c.extra, 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return nil
}

// writeMQTT writes mqtt.json (and the broker CA, when the server sent one) into
// dir. caPath is where the CA will finally live, recorded in mqtt.json.
func (f files) writeMQTT(dir string, c api.MQTTConnect, configVersion *string, keep config.Object) error {
	m := MQTT{
		Version: fileVersion, Host: c.Host, Port: c.Port, Username: c.Username, Password: c.Password,
		TLS: true, ConfigVersion: configVersion,
	}
	if c.CACert != nil && *c.CACert != "" {
		if err := config.WriteFileAtomic(filepath.Join(dir, filepath.Base(f.p.MQTTCAFile())), []byte(*c.CACert), 0o644); err != nil {
			return fmt.Errorf("identity: %w", err)
		}
		final := f.p.MQTTCAFile()
		m.CACertPath = &final
	}
	if err := config.WriteJSON(filepath.Join(dir, filepath.Base(f.p.MQTTFile())), m, keep, 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return nil
}

// autoRequest is identity/auto-request.json: the SHA-256 of an API key the
// server refused for the automatic request (never the key itself).
type autoRequest struct {
	Version          int    `json:"version"`
	RefusedKeySHA256 string `json:"refused_key_sha256"`
}

func keyHash(key string) string {
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])
}

// autoRefused reports whether the server refused key for the automatic request.
func (f files) autoRefused(key string) bool {
	var a autoRequest
	_, ok, _ := f.readJSON(f.p.AutoRequestFile(), &a)
	return ok && key != "" && a.RefusedKeySHA256 == keyHash(key)
}

func (f files) markAutoRefused(key string) error {
	a := autoRequest{Version: fileVersion, RefusedKeySHA256: keyHash(key)}
	if err := config.WriteJSON(f.p.AutoRequestFile(), a, nil, 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return nil
}

// usableMQTT reports whether the server's connect info can be stored: the
// Python agent ignored settings without a password or with TLS turned off.
func usableMQTT(c *api.MQTTConnect) bool {
	return c != nil && c.Password != "" && (c.TLS == nil || *c.TLS)
}

// commit stores a new identity atomically (see Paths.StagingDir) and removes what
// enrollment used up: the pending claim and the API key. Signing-key pins
// belong to the old organization, so they go too, as in the Python agent.
func (f files) commit(e api.Enrollment) error {
	// A finished stage of an earlier commit is moved into place first: deleting
	// it could leave a moved token next to an old device.json for good.
	if err := f.finishStaging(); err != nil {
		return err
	}
	staging := f.p.StagingDir()
	if err := os.MkdirAll(staging, 0o700); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	if err := config.WriteFileAtomic(filepath.Join(staging, filepath.Base(f.p.DeviceTokenFile())), []byte(e.DeviceToken), 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	if usableMQTT(e.MQTT) {
		if err := f.writeMQTT(staging, *e.MQTT, nil, nil); err != nil {
			return err
		}
	}
	dev, err := f.loadDevice()
	if err != nil || dev == nil {
		dev = &config.Device{} // a damaged device.json was moved aside
	}
	dev.DeviceID, dev.OrganizationID, dev.Name = e.DeviceID, e.OrganizationID, e.Name
	dev.HeartbeatIntervalSeconds = e.HeartbeatIntervalSeconds
	if err := dev.Save(filepath.Join(staging, filepath.Base(f.p.DeviceFile())), config.Owner{}); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	if err := config.WriteFileAtomic(filepath.Join(staging, doneFile), []byte("{}"), 0o600); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return f.finishStaging()
}

// finishStaging moves a finished staging directory into place, or drops an
// unfinished one. It is safe to run again after a crash halfway through.
func (f files) finishStaging() error {
	staging := f.p.StagingDir()
	if _, err := os.Stat(filepath.Join(staging, doneFile)); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			if err := os.RemoveAll(staging); err != nil {
				return fmt.Errorf("identity: %w", err)
			}
			return nil
		}
		return fmt.Errorf("identity: %w", err)
	}
	// device.json last: once it names the new device, everything else is in place.
	for _, name := range []string{"mqtt-ca.crt", "mqtt.json", "device-token", "device.json"} {
		err := os.Rename(filepath.Join(staging, name), filepath.Join(f.p.IdentityDir, name))
		if err != nil && !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("identity: %w", err)
		}
	}
	if err := f.remove(f.p.PendingClaimFile(), f.p.APIKeyFile(), f.p.AutoRequestFile(), filepath.Join(f.p.StateDir, "signing-keys.json")); err != nil {
		return err
	}
	if err := os.RemoveAll(staging); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	return nil
}
