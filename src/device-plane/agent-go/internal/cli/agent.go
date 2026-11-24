package cli

import (
	"context"
	"errors"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/modules"
	"meteorcloud/meteorcli/internal/platform"
	"meteorcloud/meteorcli/internal/state"
	"meteorcloud/meteorcli/internal/telemetry"
	"meteorcloud/meteorcli/internal/workload"
)

// runAgent is `meteorcli agent` (alias `run`): build Deps, start the supervisor
// with the registered modules, and run until SIGTERM. It keeps running when the
// device is not configured or not enrolled yet: the socket and identity modules
// run, and the others start the moment the device is enrolled.
func runAgent(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "agent", "[--debug]")
	debug := fl.Bool("debug", false, "Log debug messages.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	level := slog.LevelInfo
	if *debug {
		level = slog.LevelDebug
	}
	errs := &agent.ErrorCounter{}
	log := agent.NewCountingLogger(env.Stderr, level, errs)
	core := log.With("module", "agent")
	p := env.Paths

	c, err := config.Load(p.ConfigFile)
	if err != nil {
		if !errors.Is(err, fs.ErrNotExist) {
			core.Error("config unusable; running as not configured", "err", err)
		}
		c = nil
	}

	// The identity service sets the server (and changes it when config.json does).
	client, err := api.New(api.Options{Version: env.Version})
	if err != nil {
		core.Error("API client", "err", err)
		return 1
	}
	store, err := state.NewStore(p.StateDir)
	if err != nil {
		core.Error("state", "err", err)
		return 1
	}
	outbox, err := state.NewOutbox(filepath.Join(p.StateDir, "outbox"), 0)
	if err != nil {
		core.Error("outbox", "err", err)
		return 1
	}

	// tmpfiles.d creates them on a device; this covers --root and development.
	if err := os.MkdirAll(p.RuntimeDir, 0o750); err != nil {
		core.Error("runtime directory", "err", err)
		return 1
	}
	if err := os.MkdirAll(p.IdentityDir, 0o700); err != nil {
		core.Error("identity directory", "err", err)
		return 1
	}

	host := platform.System()
	signals, health := agent.NewSignals(), agent.NewHealth()
	source := identity.NewSource(p)
	enroller := &identity.Service{
		Source: source, API: client, Log: log.With("module", "identity"),
		Inventory: func() api.Inventory { return telemetry.Inventory(host, env.Version) },
		Events:    agent.IdentityEvents(signals, health),
	}
	// Load the identity before the supervisor asks whether the device is enrolled.
	start := enroller.Reload(ctx)

	deps := agent.Deps{
		Version: env.Version, Paths: p, Config: c, API: client, Identity: source, Host: host,
		State: store, Outbox: outbox, Signals: signals, Health: health, Log: log,
		Bus: bus.NewRouter(log.With("module", "bus")), Errors: errs,
		Helper: helper.NewClient(p.HelperSocket()), Workloads: workload.NewLock(),
	}
	sup := agent.NewSupervisor(modules.All(), deps)
	sup.Enrolled = source.Enrolled
	sock := agent.NewSocketModule(sup, env.groups(), enroller)
	if env.AgentUID != nil {
		sock.OwnUID = *env.AgentUID
	}
	sup.Add(sock)
	sup.Add(&agent.IdentityModule{Service: enroller})
	sup.Notifier = agent.NotifierFromEnv()
	core.Info("starting", "version", env.Version, "server", start.Server, "state", start.State, "board", host.Board().Name)
	if err := sup.Run(ctx); err != nil {
		core.Error("supervisor", "err", err)
		return 1
	}
	return 0
}
