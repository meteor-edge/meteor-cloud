// Package workloads is the module that tells the control plane what each
// deployed workload is made of: its containers, images, volumes and networks,
// or its systemd unit (the console's software detail page). It sends a
// snapshot when something changed and every few minutes. It only reads, through
// workload.inspect of the root helper, which answers for MeteorCloud workloads
// and nothing else. Optional: a crash loop parks this module, nothing else.
package workloads

import (
	"context"
	"errors"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/deploy"
	"meteorcloud/meteorcli/internal/workload"
)

const name = "workloads"

// Timing.
const (
	lookEvery   = time.Minute     // looks for changes
	resendEvery = 5 * time.Minute // sends although nothing changed
	lookTimeout = 45 * time.Second
)

// Module sends workload snapshots.
type Module struct {
	// helper replaces Deps.Helper (tests).
	helper caller
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return false }

// Run implements agent.Module.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	changed := d.Signals.Subscribe(agent.Is(agent.WorkloadsChanged{}))
	defer changed.Close()
	l := &looker{d: d, helper: m.helper, now: time.Now, sent: map[string]sent{}}
	if l.helper == nil {
		l.helper = noHelper{}
		if d.Helper != nil {
			l.helper = d.Helper
		}
	}
	tick := time.NewTicker(lookEvery)
	defer tick.Stop()
	for {
		d.Health.Tick(name)
		l.look(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-changed.C():
			changed.Take()
		case <-tick.C:
		}
	}
}

// sent is the last snapshot of a workload the server took.
type sent struct {
	fingerprint string
	at          time.Time
}

type looker struct {
	d      agent.Deps
	helper caller
	now    func() time.Time
	sent   map[string]sent
	// quietUntil holds the module back from a server without the route.
	quietUntil time.Time
}

func (l *looker) look(ctx context.Context) {
	now := l.now()
	if now.Before(l.quietUntil) {
		return
	}
	all, err := deploy.InstalledWorkloads(l.d.State)
	if err != nil {
		l.d.Log.Warn("read the installed workloads", "err", err)
		return
	}
	stops := workload.Stops{Store: l.d.State}
	for key, in := range all {
		cctx, cancel := context.WithTimeout(ctx, lookTimeout)
		s, err := Take(cctx, l.helper, in, stops.Get(key), now)
		cancel()
		if err != nil {
			l.d.Log.Debug("look at a workload", "workload", key, "err", err)
			continue
		}
		last, had := l.sent[key]
		fp := s.fingerprint()
		if had && last.fingerprint == fp && now.Sub(last.at) < resendEvery {
			continue
		}
		err = send(ctx, l.d, s)
		switch {
		case api.StatusOf(err) == 404: // an older server
			l.quietUntil = now.Add(time.Hour)
			return
		case api.IsAuth(err):
			return
		case err != nil:
			l.d.Log.Debug("send a workload snapshot; trying again", "workload", key, "err", err)
			continue
		}
		l.sent[key] = sent{fingerprint: fp, at: now}
	}
}

// noHelper answers every call when the agent has no helper client.
type noHelper struct{}

func (noHelper) Call(context.Context, string, any, any) error {
	return errors.New("the root helper is not available")
}
