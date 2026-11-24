package agent

import (
	"context"

	"meteorcloud/meteorcli/internal/identity"
)

// IdentityModule runs the identity service: it polls a pending enrollment
// request until it is approved, rechecks a refused token, refreshes the MQTT
// credentials and picks up identity files written by other tools. It is core
// and critical: without it a device can never become enrolled.
type IdentityModule struct {
	Service *identity.Service
}

// Name implements Module.
func (m *IdentityModule) Name() string { return "identity" }

// Critical implements Module.
func (m *IdentityModule) Critical() bool { return true }

// Run implements Module.
func (m *IdentityModule) Run(ctx context.Context, _ Deps) error { return m.Service.Run(ctx) }

// IdentityEvents turns identity's events into signals and health ticks.
func IdentityEvents(signals *Signals, health *Health) identity.Events {
	return identity.Events{
		Enrolled:           func() { signals.Publish(Enrolled{}) },
		CredentialsRotated: func() { signals.Publish(CredentialsRotated{}) },
		ConfigChanged:      func() { signals.Publish(ConfigChanged{}) },
		Tick:               func() { health.Tick("identity") },
	}
}
