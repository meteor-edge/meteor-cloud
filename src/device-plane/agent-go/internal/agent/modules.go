package agent

// Modules lists every module the agent runs. To add a feature, write a package
// with a type that implements Module and add one line here; nothing else in the
// core changes. Modules land in their own pull requests (identity, heartbeat,
// bus, actions, deploy, network, telemetry, update), so the list starts empty.
func Modules() []Module {
	return []Module{}
}
