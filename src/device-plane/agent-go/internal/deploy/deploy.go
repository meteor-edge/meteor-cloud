// Package deploy is the module that installs the software the control plane
// wants on this device (contracts/device-agent-v1.md, "Desired software").
// After every heartbeat (and on an MQTT sync_desired) it lists the desired
// items, refuses any whose signature does not verify, downloads artifacts
// (checking their SHA-256), applies them through a runtime (systemd, docker,
// docker_compose) that works through the root helper, waits for the release's
// health check and rolls back to the previous release when it fails. Results
// go into the outbox; the heartbeat carries them. It runs in its own
// goroutine, so a slow pull never delays a heartbeat.
package deploy

import (
	"context"
	"encoding/json"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/credstore"
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/signing"
	"meteorcloud/meteorcli/internal/state"
)

// name is the module's id.
const name = "deploy"

// Timeouts around runtime calls. A helper call without a deadline would be cut
// after a minute, too short for an image pull.
const (
	applyTimeout = 16 * time.Minute // the helper allows 15 for docker run / compose up
	checkTimeout = 45 * time.Second
)

// Module reconciles desired software.
type Module struct {
	// Helper replaces the root helper (tests). nil means Deps.Helper.
	Helper runtime.Caller

	d        agent.Deps
	runtimes map[string]runtime.Runtime
	kinds    map[string]kind
	applied  appliedState
	probe    func(ctx context.Context, url string) string
	clock    clock
	// events remembers the last event per workload, so a refusal repeated
	// every cycle is announced once.
	events map[string]string
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module: a broken deploy never takes the device offline.
func (m *Module) Critical() bool { return false }

// Run implements agent.Module: reconcile each time the heartbeat says so.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	waiting := d.Signals.Subscribe(agent.Is(agent.WorkWaiting{Kind: "software"}))
	defer waiting.Close()
	m.setup(d)
	d.Health.Set(name, agent.StateRunning, nil)
	for {
		d.Health.Tick(name)
		select {
		case <-ctx.Done():
			return nil
		case <-waiting.C():
			waiting.Take()
			m.reconcile(ctx)
		}
	}
}

func (m *Module) setup(d agent.Deps) {
	m.d = d
	h := m.Helper
	if h == nil {
		if d.Helper != nil {
			h = d.Helper
		} else {
			h = noHelper{}
		}
	}
	m.kinds = kinds
	m.runtimes = map[string]runtime.Runtime{}
	for k, spec := range kinds {
		m.runtimes[k] = spec.make(h, d.Paths)
	}
	m.applied = appliedState{store: d.State}
	if m.probe == nil {
		m.probe = httpProbe
	}
	if m.clock == nil {
		m.clock = realClock{}
	}
	m.events = map[string]string{}
}

// reconcile runs one cycle and keeps its reports in the outbox.
func (m *Module) reconcile(ctx context.Context) {
	d := m.d
	token, err := d.Identity.Token()
	if err != nil {
		return // not enrolled or refused: identity deals with it
	}
	applied, err := m.applied.load()
	if err != nil {
		d.Log.Error("read applied software; trying again next cycle", "err", err)
		return
	}
	c := &cycle{r: m, token: token, applied: applied, tools: m.tools(ctx),
		creds: creds{store: credstore.Store{Root: d.Paths.AppCredentialsDir()}}}
	if id := d.Identity.DeviceID(); id != "" {
		c.verifier = &signing.Verifier{
			Store: d.State, DeviceID: id, Fingerprint: signing.CredentialFingerprint(token), Log: d.Log, Stop: api.IsAuth,
			Fetch: func(ctx context.Context) ([]signing.Key, error) { return m.signingKeys(ctx, token) },
		}
	}
	reports, attempts := c.run(ctx)
	var outcomes []report
	var ids []string
	for _, r := range reports {
		key, _ := r["workload_key"].(string)
		kept, err := d.Outbox.Put(state.KindSoftwareReport, key, r)
		if err != nil {
			d.Log.Error("keep software report", "workload", key, "err", err)
		}
		if attempts[key] {
			m.event(ctx, r)
			outcomes = append(outcomes, r)
			if err == nil {
				ids = append(ids, kept.ID)
			}
		}
	}
	m.sendNow(ctx, token, outcomes, ids)
}

// sendNow sends the outcome of install attempts at once, in a heartbeat that
// carries only software_report (as the Python agent reported in the same
// cycle), instead of waiting up to a heartbeat interval. The outbox keeps them
// until the server accepted them; health-only reports wait for the heartbeat.
func (m *Module) sendNow(ctx context.Context, token string, outcomes []report, ids []string) {
	if len(outcomes) == 0 {
		return
	}
	body, err := jsonRaw(outcomes...)
	if err != nil {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, progressTimeout)
	defer cancel()
	_, err = m.d.API.Heartbeat(ctx, token, api.HeartbeatRequest{SoftwareReport: body})
	switch {
	case api.IsAuth(err):
		m.d.Identity.AuthFailed(token)
	case err != nil:
		m.d.Log.Warn("could not send deploy results now; the next heartbeat carries them", "err", err)
	default:
		if err := m.d.Outbox.Ack(ids...); err != nil {
			m.d.Log.Error("remove sent software reports", "err", err)
		}
	}
}

func (m *Module) tools(ctx context.Context) map[string]bool {
	t := m.d.Host.Tools(ctx)
	out := map[string]bool{}
	for k, spec := range m.kinds {
		out[k] = spec.installed(t)
	}
	return out
}

func (m *Module) signingKeys(ctx context.Context, token string) ([]signing.Key, error) {
	keys, err := m.d.API.SigningKeys(ctx, token)
	if api.StatusOf(err) == 404 {
		return nil, signing.ErrNotSigning
	}
	if err != nil {
		return nil, err
	}
	out := make([]signing.Key, 0, len(keys))
	for _, k := range keys {
		sk := signing.Key{KeyID: k.KeyID, PublicKey: k.PublicKey}
		if k.EndorsedBy != nil {
			sk.EndorsedBy = *k.EndorsedBy
		}
		if k.Endorsement != nil {
			sk.Endorsement = *k.Endorsement
		}
		out = append(out, sk)
	}
	return out, nil
}

// event announces the outcome of an install attempt on devices/{id}/events
// (type deploy.in_sync, deploy.failed or deploy.rolled_back), once per change.
// Best effort: without a broker connection it is skipped.
func (m *Module) event(ctx context.Context, r report) {
	key, _ := r["workload_key"].(string)
	ev := map[string]any{
		"type": "deploy." + r.status(), "timestamp": bus.Timestamp(time.Now()), "workload_key": key,
		"health": r.health(),
	}
	if v, ok := r["installed_version"]; ok {
		ev["release_version"] = v
	}
	if e := r.lastError(); e != "" {
		ev["error"] = e
	}
	seen := r.status() + "|" + runtime.Stringify(r["installed_release_id"]) + "|" + r.lastError()
	if m.events[key] == seen {
		return
	}
	m.events[key] = seen
	if err := m.d.Bus.PublishJSON(ctx, "events", ev, false); err != nil {
		m.d.Log.Debug("deploy event not sent", "workload", key, "err", err)
	}
}

// jsonRaw encodes reports for a heartbeat body.
func jsonRaw(reports ...report) ([]json.RawMessage, error) {
	out := make([]json.RawMessage, 0, len(reports))
	for _, r := range reports {
		b, err := json.Marshal(r)
		if err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, nil
}

// noHelper answers every call when the agent has no helper client.
type noHelper struct{}

func (noHelper) Call(context.Context, string, any, any) error {
	return runtime.Failf("the root helper is not available (is meteorcli-helper.socket installed?)")
}
