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
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/state"
)

// runAgent is `meteorcli agent` (alias `run`): build Deps, start the supervisor
// with the registered modules, and run until SIGTERM. It keeps running when the
// device is not configured yet: the modules wait instead of the process exiting.
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
	log := agent.NewLogger(env.Stderr, level)
	core := log.With("module", "agent")
	p := env.Paths

	if config.LooksLikePython(p.LegacyDir) {
		core.Warn("a Python meteorcli layout is still in place; run 'sudo meteorcli config import'", "dir", p.LegacyDir)
	}
	c, err := config.Load(p.ConfigFile)
	if err != nil {
		if !errors.Is(err, fs.ErrNotExist) {
			core.Error("config unusable; running as not configured", "err", err)
		}
		c = nil
	}

	var client *api.Client
	if server := c.Server(); server != "" {
		client, err = api.New(api.Options{BaseURL: server, Version: env.Version, CAFile: caFile(c)})
		if err != nil {
			core.Error("API client", "err", err)
			return 1
		}
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

	// tmpfiles.d creates it on a device; this covers --root and development.
	if err := os.MkdirAll(p.RuntimeDir, 0o750); err != nil {
		core.Error("runtime directory", "err", err)
		return 1
	}

	deps := agent.Deps{
		Version: env.Version, Paths: p, Config: c, API: client, State: store, Outbox: outbox,
		Signals: agent.NewSignals(), Health: agent.NewHealth(), Log: log,
		Helper: helper.NewClient(p.HelperSocket()),
	}
	sup := agent.NewSupervisor(agent.Modules(), deps)
	sup.Add(agent.NewSocketModule(sup, env.groups()))
	sup.Notifier = agent.NotifierFromEnv()
	core.Info("starting", "version", env.Version, "server", c.Server())
	if err := sup.Run(ctx); err != nil {
		core.Error("supervisor", "err", err)
		return 1
	}
	return 0
}

func caFile(c *config.Config) string {
	if c == nil {
		return ""
	}
	return c.CAFile
}
