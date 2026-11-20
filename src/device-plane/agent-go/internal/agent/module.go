// Package agent is the core of the always-on service: the Module interface, the
// shared Deps, the supervisor that runs modules and restarts them when they fail,
// the crash-loop breaker, the health registry, the signals hub, the log redactor
// and the systemd notify/watchdog link. Features live in their own packages and
// are listed in modules.go; the core does not change when a feature is added.
package agent

import (
	"context"
	"log/slog"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/platform"
	"meteorcloud/meteorcli/internal/state"
)

// Module is one feature of the agent (heartbeat, deploy, telemetry, ...). The
// supervisor runs each module in its own goroutine and restarts it when Run
// returns an error or panics.
type Module interface {
	// Name is the module's id in logs, health and `meteorcli module restart`.
	Name() string
	// Critical modules (identity, heartbeat, mqtt) keep the device reachable and
	// are never parked by the crash-loop breaker.
	Critical() bool
	// Run does the module's work until ctx is cancelled, then returns ctx.Err()
	// or nil. Any other return counts as a failure.
	Run(ctx context.Context, d Deps) error
}

// Deps is everything a module may use. Modules never call each other: they share
// only what is here, talk through Signals, and leave reports in the Outbox.
// Bus is added with its package.
type Deps struct {
	// Version is this agent's version (status, User-Agent).
	Version string
	// Paths says where files live.
	Paths config.Paths
	// Config is the operator's settings as read at start. nil until configured.
	// The identity module reloads the server when config.json changes and
	// publishes ConfigChanged; modules that need a current value reread it then.
	Config *config.Config
	// API calls the control plane. It always exists; calls fail with
	// api.ErrNoServer until a server is configured.
	API *api.Client
	// Identity is the device token and MQTT credentials. Report a 401/403
	// with Identity.AuthFailed; never act on it yourself.
	Identity *identity.Source
	// Host reads facts about this machine (inventory, metrics, tools).
	Host *platform.Host
	// State keeps small versioned files across restarts.
	State *state.Store
	// Outbox keeps reports until the server accepted them.
	Outbox *state.Outbox
	// Signals tells other modules that something happened.
	Signals *Signals
	// Helper does privileged work (root) from an allowlist.
	Helper *helper.Client
	// Health is where a module reports progress (Tick) and state.
	Health *Health
	// Log already carries module=<name>.
	Log *slog.Logger
}
