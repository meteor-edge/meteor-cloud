package cli

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/state"
	"meteorcloud/meteorcli/internal/update"
)

// runUpdater is `meteorcli updater`: the root process the helper starts as
// the transient unit meteorcli-update.service (update.start). Root may also
// run it by hand to update at once and watch it.
func runUpdater(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "updater", "[--action ID] [--health-timeout 3m] [--bin PATH]")
	action := fl.String("action", "", "The update_agent action the outcome belongs to (set by the helper).")
	timeout := fl.Duration("health-timeout", update.DefaultHealthTimeout, "How long the new version has to send its first heartbeat.")
	bin := fl.String("bin", "/usr/local/bin/meteorcli", "The installed meteorcli to replace.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	log := agent.NewLogger(env.Stderr, slog.LevelInfo).With("module", "updater")
	if os.Geteuid() != 0 && env.Paths.Root == "" {
		log.Error("the updater must run as root: sudo meteorcli updater (or 'meteorcli update' as a meteorcli-admin)")
		return 1
	}
	p := env.Paths
	report := func(o update.Outcome) int {
		o.ActionID = *action
		if err := update.WriteOutcome(p.StateDir, o, env.Owner); err != nil {
			log.Error("write the update outcome", "err", err)
		}
		if o.Done() {
			fmt.Fprintf(env.Stdout, "meteorcli %s: %s\n", o.ToVersion, o.Status)
			return 0
		}
		fmt.Fprintf(env.Stderr, "meteorcli update %s: %s\n", o.Status, o.Error)
		return 1
	}
	failed := func(err error) int {
		log.Error("update failed", "err", err)
		return report(update.Outcome{Status: update.StatusFailed, FromVersion: env.Version, Error: err.Error(), FinishedAt: time.Now().UTC()})
	}

	unlock, err := update.Lock(filepath.Join(p.Root, "/run/meteorcli-update/lock"))
	if err != nil {
		if errors.Is(err, update.ErrBusy) && *action == "" {
			fmt.Fprintln(env.Stderr, "error: another meteorcli update is running (journalctl -u meteorcli-update -f)")
			return 1
		}
		return failed(err)
	}
	defer unlock()
	if *action == "" {
		// One outcome file: a console update waiting for its result keeps it.
		if st, err := state.NewStore(p.StateDir); err == nil {
			if err := update.CheckNoPending(st); err != nil {
				fmt.Fprintf(env.Stderr, "error: %v; try again when it has finished\n", err)
				return 1
			}
		}
	}

	u, err := newUpdater(env, filepath.Join(p.Root, *bin), *timeout, log)
	if err != nil {
		return failed(err)
	}
	return report(u.Run(ctx))
}

// newUpdater builds the updater from config.json and this build's keys.
func newUpdater(env *Env, bin string, timeout time.Duration, log *slog.Logger) (*update.Updater, error) {
	c, err := config.Load(env.Paths.ConfigFile)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	keys, err := update.ParseKeys(update.ReleaseKeys())
	if err != nil {
		return nil, err
	}
	var caFile string
	if c != nil {
		caFile = c.CAFile
	}
	tlsCfg, err := api.TLSConfig(caFile)
	if err != nil {
		return nil, err
	}
	fetch, err := update.NewHTTPFetcher(c.Release(), "meteorcli/"+env.Version, tlsCfg)
	if err != nil {
		return nil, err
	}
	asset, err := update.AssetName()
	if err != nil {
		return nil, err
	}
	log.Info("checking for a new release", "url", c.Release(), "installed", env.Version)
	return &update.Updater{
		Fetch: fetch, Keys: keys, Asset: asset, Bin: bin, Current: env.Version,
		System: update.Systemctl{}, Probe: update.SocketProbe(env.Paths.AgentSocket()),
		Smoke: update.SmokeVersion, FreeBytes: update.FreeBytes,
		HealthTimeout: timeout, Log: log,
	}, nil
}
