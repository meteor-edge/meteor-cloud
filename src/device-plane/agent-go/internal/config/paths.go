// Package config owns where meteorcli keeps its files, the non-secret settings in
// /etc/meteorcli/config.json, atomic file writes, and the one-time import of the
// layout the Python agent used.
package config

import (
	"os"
	"path/filepath"
)

// RootEnv names the environment variable that moves every path under a prefix.
// Tests and local development use it so nothing touches the real system.
const RootEnv = "METEORCLI_ROOT"

// Paths lists every file and directory meteorcli uses. Code asks Paths instead of
// building paths itself, so the layout is written down in exactly one place.
type Paths struct {
	// Root is "" on a device and a scratch directory in tests.
	Root string
	// ConfigFile holds non-secret settings (server, pinned CA). root:meteor 0640.
	ConfigFile string
	// LegacyDir is where the Python agent kept config and secrets together.
	LegacyDir string
	// IdentityDir holds the device token, MQTT credentials and enrollment secrets. meteor 0700.
	IdentityDir string
	// StateDir holds versioned module state and the outbox. meteor 0700.
	StateDir string
	// ArtifactsDir holds downloaded release artifacts. meteor 0750.
	ArtifactsDir string
	// RuntimeDir holds the sockets. meteor:meteorcli 0750.
	RuntimeDir string
}

// DefaultPaths returns the device layout, moved under root when root is not "".
func DefaultPaths(root string) Paths {
	at := func(p string) string { return filepath.Join(root, p) }
	return Paths{
		Root:         root,
		ConfigFile:   at("/etc/meteorcli/config.json"),
		LegacyDir:    at("/etc/meteorcli"),
		IdentityDir:  at("/var/lib/meteorcli/identity"),
		StateDir:     at("/var/lib/meteorcli/state"),
		ArtifactsDir: at("/var/lib/meteorcli/artifacts"),
		RuntimeDir:   at("/run/meteorcli"),
	}
}

// PathsFromEnv returns DefaultPaths under $METEORCLI_ROOT (empty on a device).
func PathsFromEnv() Paths { return DefaultPaths(os.Getenv(RootEnv)) }

// AgentSocket is where the CLI talks to the agent.
func (p Paths) AgentSocket() string { return filepath.Join(p.RuntimeDir, "agent.sock") }

// HelperSocket is where the agent asks the root helper for privileged work.
func (p Paths) HelperSocket() string { return filepath.Join(p.RuntimeDir, "helper.sock") }

// DeviceFile holds the non-secret enrollment facts (device id, organization, name).
func (p Paths) DeviceFile() string { return filepath.Join(p.IdentityDir, "device.json") }

// DeviceTokenFile holds the device bearer token.
func (p Paths) DeviceTokenFile() string { return filepath.Join(p.IdentityDir, "device-token") }

// MQTTFile holds the MQTT connect settings including the password.
func (p Paths) MQTTFile() string { return filepath.Join(p.IdentityDir, "mqtt.json") }

// MQTTCAFile holds the broker CA when it is not a public CA.
func (p Paths) MQTTCAFile() string { return filepath.Join(p.IdentityDir, "mqtt-ca.crt") }

// APIKeyFile holds an enrollment API key until enrollment succeeds.
func (p Paths) APIKeyFile() string { return filepath.Join(p.IdentityDir, "api-key") }

// PendingClaimFile holds the request id and claim secret of an enrollment awaiting approval.
func (p Paths) PendingClaimFile() string { return filepath.Join(p.IdentityDir, "pending-claim.json") }

// StagingDir is where the agent assembles a new identity before moving it into
// place (a finished one has a DONE file). It belongs to the server too.
func (p Paths) StagingDir() string { return filepath.Join(p.IdentityDir, ".staging") }

// AutoRequestFile records that the server refused the saved API key for the
// request the agent makes by itself, so it is not sent again at every start.
func (p Paths) AutoRequestFile() string { return filepath.Join(p.IdentityDir, "auto-request.json") }

// WireGuardDir holds the WireGuard key pair. The private key never leaves the device.
func (p Paths) WireGuardDir() string { return filepath.Join(p.IdentityDir, "wireguard") }

// EnrollmentFiles are the files that belong to one server. Changing the server
// removes them so the old token is never sent to a new server.
func (p Paths) EnrollmentFiles() []string {
	return []string{
		p.DeviceTokenFile(), p.MQTTFile(), p.MQTTCAFile(), p.PendingClaimFile(), p.APIKeyFile(), p.DeviceFile(),
		p.AutoRequestFile(),
	}
}
