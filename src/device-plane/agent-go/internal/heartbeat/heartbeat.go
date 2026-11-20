// Package heartbeat is the module that tells the control plane this device is
// alive: POST /api/v1/agent/heartbeat with the inventory, the capabilities
// (tools, actions, deploy kinds and module health) and the software reports
// waiting in the outbox. The answer says when to beat next and which work
// waits; the heartbeat only passes that on as signals and never does the work
// itself, so a slow deploy can never take the device offline.
package heartbeat

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/state"
	"meteorcloud/meteorcli/internal/telemetry"
)

// name is the module's id.
const name = "heartbeat"

// Limits of one loop.
const (
	// idle is how long to wait for an identity before looking again. The
	// Enrolled signal usually ends the wait at once.
	idle = 30 * time.Second
	// tickEvery keeps the watchdog fed during long intervals.
	tickEvery = 30 * time.Second
	// maxReports bounds the software reports sent in one heartbeat; the rest
	// go with the next one.
	maxReports = 100
)

// Module sends heartbeats. It is critical: it keeps the device reachable.
type Module struct {
	// Actions and DeployKinds are what this agent's code can do, reported in
	// capabilities. The module registry fills them from the action and deploy
	// registries, so this package imports neither.
	Actions     []string
	DeployKinds []string
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return true }

// Run implements agent.Module: beat, wait the interval the server asked for
// (or the retry backoff), and beat again at once when the device is enrolled anew.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	enrolled := d.Signals.Subscribe(agent.Is(agent.Enrolled{}))
	defer enrolled.Close()
	l := &loop{m: m, d: d, tools: &telemetry.Tools{Host: d.Host}, backoff: api.NewBackoff()}
	for {
		d.Health.Tick(name)
		wait := l.beat(ctx)
		if !l.sleep(ctx, enrolled, wait) {
			return nil
		}
	}
}

// loop is one Run's state.
type loop struct {
	m       *Module
	d       agent.Deps
	tools   *telemetry.Tools
	backoff *api.Backoff
	online  bool // the last heartbeat succeeded (log the change only)
}

// beat sends one heartbeat and returns how long to wait before the next.
func (l *loop) beat(ctx context.Context) time.Duration {
	d := l.d
	token, err := d.Identity.Token()
	if err != nil {
		d.Health.Set(name, agent.StateWaiting, nil)
		if l.online || !errors.Is(err, identity.ErrNotEnrolled) {
			d.Log.Info("heartbeat paused", "reason", err)
		}
		l.online = false
		return idle
	}
	d.Health.Set(name, agent.StateRunning, nil)

	reports, err := d.Outbox.Pending(state.KindSoftwareReport)
	if err != nil {
		d.Log.Error("read software reports; sending the heartbeat without them", "err", err)
		reports = nil
	}
	if len(reports) > maxReports {
		reports = reports[:maxReports]
	}
	req := api.HeartbeatRequest{
		Inventory:    telemetry.Inventory(d.Host, d.Version),
		Capabilities: l.capabilities(ctx),
	}
	if len(reports) > 0 {
		req.SoftwareReport = make([]json.RawMessage, len(reports))
		for i, r := range reports {
			req.SoftwareReport[i] = r.Body
		}
	}

	resp, err := d.API.Heartbeat(ctx, token, req)
	switch {
	case api.IsAuth(err):
		// Identity decides what a refused token means and when to try it again.
		// Identity publishes Enrolled once the server takes the token again.
		d.Identity.AuthFailed(token)
		d.Health.Set(name, agent.StateWaiting, err)
		d.Log.Warn("heartbeat refused the device token; waiting for identity", "err", err)
		l.online = false
		return idle
	case err != nil:
		wait := l.backoff.Next(err)
		d.Log.Warn("heartbeat failed", "err", err, "retry_in", wait.Round(time.Millisecond))
		l.online = false
		return wait
	}
	l.backoff.Reset()
	if !l.online {
		d.Log.Info("heartbeat ok; device online", "device_id", resp.DeviceID, "status", resp.Status)
		l.online = true
	}
	if len(reports) > 0 {
		ids := make([]string, len(reports))
		for i, r := range reports {
			ids[i] = r.ID
		}
		if err := d.Outbox.Ack(ids...); err != nil {
			d.Log.Error("remove sent software reports", "err", err)
		}
	}
	if err := d.Identity.SetHeartbeat(resp.HeartbeatIntervalSeconds); err != nil {
		d.Log.Warn("store heartbeat interval", "err", err)
	}
	// Desired software is listed on every cycle; the other kinds only when waiting.
	d.Signals.Publish(agent.WorkWaiting{Kind: "software"})
	if resp.ActionsPending {
		d.Signals.Publish(agent.WorkWaiting{Kind: "actions"})
	}
	if resp.WifiPending {
		d.Signals.Publish(agent.WorkWaiting{Kind: "wifi"})
	}
	return d.Identity.Heartbeat()
}

// capabilities reports the installed tools, what this agent can do, and the
// health of every module.
func (l *loop) capabilities(ctx context.Context) *api.Capabilities {
	t := l.tools.Get(ctx)
	return &api.Capabilities{
		Systemd: t.Systemd, Docker: t.Docker, DockerCompose: t.DockerCompose,
		WireGuard: t.WireGuard, SSH: t.SSH,
		Actions: l.m.Actions, DeployKinds: l.m.DeployKinds,
		Modules: l.d.Health.Snapshot(),
	}
}

// sleep waits for wait, ticking the health registry, and ends early when the
// device is enrolled anew. It returns false when ctx ended.
func (l *loop) sleep(ctx context.Context, enrolled *agent.Subscription, wait time.Duration) bool {
	timer := time.NewTimer(wait)
	defer timer.Stop()
	tick := time.NewTicker(tickEvery)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-timer.C:
			return true
		case <-enrolled.C():
			enrolled.Take()
			l.backoff.Reset()
			return true
		case <-tick.C:
			l.d.Health.Tick(name)
		}
	}
}
