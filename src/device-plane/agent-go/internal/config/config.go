package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Version is the config.json format this agent writes. Files without a version
// field come from the Python agent and are converted by ImportPython.
const Version = 1

// DefaultHeartbeatSeconds is used until the server says otherwise.
const DefaultHeartbeatSeconds = 60

// MinHeartbeatSeconds stops a damaged value from spinning the heartbeat loop.
const MinHeartbeatSeconds = 5

// Config is the non-secret settings an operator chooses: which server to talk to
// and which CA to trust. Root writes it; the agent only reads it.
type Config struct {
	Version int `json:"version"`
	// Domain is what the operator typed (host, IP or URL).
	Domain string `json:"domain"`
	// APIBase overrides the origin derived from Domain.
	APIBase string `json:"api_base"`
	// ServerURL is the resolved origin, kept for tools that read the file directly.
	ServerURL string `json:"server_url"`
	// CAFile is an optional PEM file trusted in addition to the system CAs.
	CAFile string `json:"ca_file"`
	// EnrollName is the device name to ask for when the agent submits an
	// enrollment request by itself (from a meteorenv file at first boot).
	EnrollName string `json:"enroll_name"`

	extra Object
}

// Load reads config.json. A missing file returns an error matching fs.ErrNotExist.
// A damaged file is moved aside and ErrCorrupt is returned: the agent then runs
// as not configured instead of crashing on every start.
func Load(path string) (*Config, error) {
	var c Config
	raw, err := ReadJSON(path, &c)
	if errors.Is(err, ErrCorrupt) {
		if dst, qerr := Quarantine(path, time.Now()); qerr == nil {
			return nil, fmt.Errorf("%w (moved to %s)", err, dst)
		}
		return nil, err
	}
	if err != nil {
		return nil, err
	}
	c.extra = raw
	return &c, nil
}

// Save writes config.json atomically (0640, root:meteor when owner is enabled),
// keeping keys this version does not know.
func (c *Config) Save(path string, owner Owner) error {
	c.Version = Version
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("save config: %w", err)
	}
	if err := WriteJSON(path, c, c.extra, 0o640); err != nil {
		return fmt.Errorf("save config: %w", err)
	}
	return owner.ConfigFile(path)
}

// Server returns the API origin, or "" when no server is configured. Same order
// as the Python agent: api_base, then domain, then server_url.
func (c *Config) Server() string {
	if c == nil {
		return ""
	}
	for _, v := range []string{c.APIBase, c.Domain, c.ServerURL} {
		if strings.TrimSpace(v) == "" {
			continue
		}
		if base, err := DeriveAPIBase(v, false); err == nil {
			return strings.TrimRight(base, "/")
		}
	}
	return ""
}

// SetServer applies `meteorcli config --domain/--api-base/--http` like the Python
// agent and reports whether the resolved server changed.
func (c *Config) SetServer(domain, apiBase string, forceHTTP bool) (changed bool, err error) {
	before := c.Server()
	if domain != "" {
		c.Domain = domain
		if apiBase == "" && !forceHTTP {
			c.APIBase = ""
		}
	}
	switch {
	case apiBase != "":
		if c.APIBase, err = DeriveAPIBase(apiBase, forceHTTP); err != nil {
			return false, err
		}
	case forceHTTP && c.Domain != "":
		if c.APIBase, err = DeriveAPIBase(c.Domain, true); err != nil {
			return false, err
		}
	}
	after := c.Server()
	if after != "" {
		c.ServerURL = after
	}
	return before != "" && after != "" && before != after, nil
}

// Device is the non-secret result of enrollment. The identity module writes it;
// status and the import read it.
type Device struct {
	Version                  int    `json:"version"`
	DeviceID                 string `json:"device_id"`
	OrganizationID           string `json:"organization_id"`
	Name                     string `json:"name"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`

	extra Object
}

// LoadDevice reads device.json. Errors match fs.ErrNotExist or ErrCorrupt.
func LoadDevice(path string) (*Device, error) {
	var d Device
	raw, err := ReadJSON(path, &d)
	if err != nil {
		return nil, err
	}
	d.extra = raw
	return &d, nil
}

// Heartbeat returns the heartbeat interval, never below MinHeartbeatSeconds.
func (d *Device) Heartbeat() time.Duration {
	s := DefaultHeartbeatSeconds
	if d != nil && d.HeartbeatIntervalSeconds > 0 {
		s = max(MinHeartbeatSeconds, d.HeartbeatIntervalSeconds)
	}
	return time.Duration(s) * time.Second
}

// Save writes device.json (0600) and hands it to the agent user.
func (d *Device) Save(path string, owner Owner) error {
	d.Version = Version
	if err := WriteJSON(path, d, d.extra, 0o600); err != nil {
		return fmt.Errorf("save device: %w", err)
	}
	return owner.Agent(path)
}

// DropEnrollment removes the files that belong to the previous server and returns
// the names it removed. Python did the same in drop_stale_enrollment.
func DropEnrollment(p Paths) ([]string, error) {
	var removed []string
	// A finished but not yet moved identity belongs to the old server as well.
	if err := os.RemoveAll(p.StagingDir()); err != nil {
		return removed, fmt.Errorf("drop enrollment: %w", err)
	}
	for _, f := range p.EnrollmentFiles() {
		err := os.Remove(f)
		switch {
		case err == nil:
			removed = append(removed, filepath.Base(f))
		case errors.Is(err, fs.ErrNotExist):
		default:
			return removed, fmt.Errorf("drop enrollment: %w", err)
		}
	}
	return removed, nil
}
