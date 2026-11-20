// Package modules is the registry of the agent's feature modules. To add a
// feature, write a package with a type that implements agent.Module and add
// one line to All; nothing in the core changes.
//
// The list lives outside package agent because every module imports agent (for
// Deps and signals); a list inside agent would be an import cycle.
package modules

import (
	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/heartbeat"
)

// All returns a fresh instance of every feature module, in no particular
// order. The supervisor starts them once the device is enrolled. The core
// modules (socket, identity) are not listed: they run before enrollment.
func All() []agent.Module {
	return []agent.Module{
		// Actions and deploy kinds come from their registries in later pull requests.
		&heartbeat.Module{},
	}
}
