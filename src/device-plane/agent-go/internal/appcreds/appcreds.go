// Package appcreds is the module that keeps the SDK tokens of bound workloads
// fresh. Deploy obtains a workload's first application credential when it
// starts the workload (internal/credstore); this module rotates every stored
// token once the server's refresh_after has passed, in place, so a running
// container sees the new token without a restart, and without depending on
// the deploy module being healthy.
package appcreds

import (
	"context"
	"errors"
	"io/fs"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/credstore"
)

const (
	name = "appcreds"
	// every is how often stored tokens are checked.
	every = time.Minute
)

// Module rotates application tokens.
type Module struct {
	// Every replaces the check interval (tests).
	Every time.Duration
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return false }

// Run implements agent.Module: check now, then every minute.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	interval := every
	if m.Every > 0 {
		interval = m.Every
	}
	store := credstore.Store{Root: d.Paths.AppCredentialsDir()}
	d.Health.Set(name, agent.StateRunning, nil)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		d.Health.Tick(name)
		Rotate(ctx, d, store)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// Rotate replaces every stored token whose refresh time has passed. A failure
// is logged and tried again on the next round; the stored token keeps working
// until it expires.
func Rotate(ctx context.Context, d agent.Deps, store credstore.Store) {
	for _, m := range store.All() {
		if !store.RefreshDue(m.WorkloadKey) {
			continue
		}
		token, err := d.Identity.Token()
		if err != nil {
			return // not enrolled or refused: identity deals with it
		}
		b := credstore.Binding{Application: m.Application, Environment: m.Environment}
		_, err = store.Mint(ctx, d.API, token, m.WorkloadKey, b, true)
		switch {
		case err == nil:
			d.Log.Info("application token rotated", "workload", m.WorkloadKey)
		case api.IsAuth(err):
			d.Identity.AuthFailed(token)
			return
		case errors.Is(err, fs.ErrNotExist):
			// deploy removed the workload meanwhile
		default:
			d.Log.Warn("application token was not rotated; trying again later", "workload", m.WorkloadKey, "err", err)
		}
	}
}
