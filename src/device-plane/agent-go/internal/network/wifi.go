package network

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
)

// The wifi module: when the heartbeat says Wi-Fi changes wait, it collects them
// (the only moment a password travels), hands each to the helper, and reports
// what happened together with the device's Wi-Fi state. A change marked
// "connect now" is kept only if MeteorCloud answers on the new network;
// otherwise the old settings come back. Passwords are never logged or stored
// by the agent. Results wait in the outbox until the server took them.

// helperRetry spaces retried confirm and rollback calls (a variable for tests).
var helperRetry = 2 * time.Second

// KindWifiResult is the outbox kind of Wi-Fi results.
const KindWifiResult = "wifi_result"

const (
	wifiName = "wifi"
	// verifyFor is how long MeteorCloud may take to answer on a new network.
	verifyFor   = 90 * time.Second
	verifyPause = 5 * time.Second
	healthCall  = 10 * time.Second
	// statusEvery is how often the state is checked; it is reported when it
	// changed and at least every statusRefresh.
	statusEvery   = time.Minute
	statusRefresh = 5 * time.Minute
	// firstStatus delays the first report, so changes waiting at enrollment
	// go out in the same report as the state (as the Python agent's first
	// heartbeat did).
	firstStatus = 15 * time.Second
	// helperTries calls to confirm or roll back, helperRetry apart.
	helperTries  = 3
	maxResultErr = 500
)

// Messages the console shows, as the Python agent wrote them.
const (
	errUnreachable = "The device joined the network but could not reach MeteorCloud through it. The old Wi-Fi settings were put back."
	errInterrupted = "The device restarted before the new network was confirmed. The old Wi-Fi settings were put back."
)

// reportKeys are the helper's status keys the server takes (network-wifi-report.request.json).
var reportKeys = []string{"supported", "unsupported_reason", "connected_ssid", "signal_percent", "interface",
	"wired_connected", "saved_networks", "uplink_type", "uplink_interface"}

// WiFi applies Wi-Fi changes and keeps the console's view of the device's Wi-Fi current.
type WiFi struct {
	// verifyFor and verifyPause, when set, replace the defaults (tests).
	verifyFor, verifyPause, firstStatus time.Duration
}

// Name implements agent.Module.
func (m *WiFi) Name() string { return wifiName }

// Critical implements agent.Module.
func (m *WiFi) Critical() bool { return false }

// Run implements agent.Module.
func (m *WiFi) Run(ctx context.Context, d agent.Deps) error {
	waiting := d.Signals.Subscribe(agent.Is(agent.WorkWaiting{Kind: "wifi"}))
	defer waiting.Close()
	w := &wifi{d: d, helper: noHelper{}, verifyFor: verifyFor, verifyPause: verifyPause}
	if d.Helper != nil {
		w.helper = d.Helper
	}
	if m.verifyFor > 0 {
		w.verifyFor, w.verifyPause = m.verifyFor, m.verifyPause
	}
	first := firstStatus
	if m.firstStatus > 0 {
		first = m.firstStatus
	}
	d.Health.Set(wifiName, agent.StateRunning, nil)
	w.recover(ctx)
	timer := time.NewTimer(first)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-waiting.C():
			waiting.Take()
			w.collect(ctx)
			w.report(ctx, true)
		case <-timer.C:
			w.recover(ctx) // a rollback that failed earlier is tried again
			w.report(ctx, false)
			timer.Reset(statusEvery)
		}
	}
}

// wifi is one Run's state.
type wifi struct {
	d                      agent.Deps
	helper                 caller
	verifyFor, verifyPause time.Duration
	lastStatus             []byte // the last state the server took, as sent
	lastAt                 time.Time
}

// recover rolls back a connect_now change a restart or power cut left
// unconfirmed, and reports it rolled back.
func (w *wifi) recover(ctx context.Context) {
	var st helper.WifiStatus
	if err := w.helper.Call(ctx, "wifi.status", nil, &st); err != nil || st.Pending == nil {
		return
	}
	w.d.Log.Warn("an unconfirmed Wi-Fi change was found; putting the old settings back")
	if err := w.rollback(ctx); err != nil {
		w.d.Log.Error("roll back the unconfirmed Wi-Fi change; trying again later", "err", err)
		return
	}
	if st.Pending.ID != "" && st.Pending.Revision != nil {
		w.keep(api.WifiResult{ID: st.Pending.ID, Revision: st.Pending.Revision, State: api.WifiRolledBack, Error: errInterrupted})
	}
}

// collect fetches the waiting changes and applies them in order.
func (w *wifi) collect(ctx context.Context) {
	d := w.d
	token, err := d.Identity.Token()
	if err != nil {
		return
	}
	changes, err := d.API.WifiChanges(ctx, token)
	switch {
	case api.IsAuth(err):
		d.Identity.AuthFailed(token)
		return
	case api.StatusOf(err) == 404:
		return // a server from before Wi-Fi management
	case err != nil:
		d.Log.Warn("could not collect Wi-Fi changes; trying again after the next heartbeat", "err", err)
		return
	}
	for _, c := range changes {
		w.keep(w.apply(ctx, c))
	}
}

// keep puts a result in the outbox; it is sent with the next report.
func (w *wifi) keep(r api.WifiResult) {
	r.Error = cut(agent.Redact(r.Error), maxResultErr)
	if _, err := w.d.Outbox.Put(KindWifiResult, "", r); err != nil {
		w.d.Log.Error("keep a Wi-Fi result", "id", r.ID, "err", err)
	}
}

func (w *wifi) apply(ctx context.Context, c api.WifiChange) api.WifiResult {
	d := w.d
	res := api.WifiResult{ID: c.ID, Revision: c.Revision}
	fail := func(state string, err error) api.WifiResult {
		res.State = state
		var e *localrpc.Error
		if errors.As(err, &e) {
			res.Error = e.Message
		} else if err != nil {
			res.Error = err.Error()
		}
		return res
	}
	switch c.Action {
	case "", "apply", "remove":
	default:
		d.Log.Warn("unknown Wi-Fi action; nothing changed", "action", c.Action, "ssid", c.SSID)
		return fail(api.WifiFailed, errors.New("Unsupported Wi-Fi action "+c.Action))
	}
	if c.Action == "remove" {
		if err := w.helper.Call(ctx, "wifi.remove", helper.WifiRemoveRequest{SSID: c.SSID}, nil); err != nil {
			d.Log.Warn("Wi-Fi network not removed", "ssid", c.SSID, "err", err)
			return fail(api.WifiFailed, err)
		}
		d.Log.Info("Wi-Fi network removed", "ssid", c.SSID)
		res.State = api.WifiRemoved
		return res
	}
	req := helper.WifiApplyRequest{ID: c.ID, Revision: c.Revision, SSID: c.SSID, Security: c.Security,
		Hidden: c.Hidden, ConnectNow: c.ConnectNow}
	if c.Password != nil {
		req.Password = *c.Password
	}
	if req.Security == "" {
		req.Security = "wpa-psk"
	}
	err := w.helper.Call(ctx, "wifi.apply", req, nil)
	switch {
	case localrpc.CodeOf(err) == helper.CodeNotJoined:
		d.Log.Warn("Wi-Fi change not kept: the network could not be joined", "ssid", c.SSID)
		return fail(api.WifiRolledBack, err)
	case err != nil:
		d.Log.Warn("Wi-Fi change failed", "ssid", c.SSID, "err", err)
		return fail(api.WifiFailed, err)
	case !c.ConnectNow:
		d.Log.Info("Wi-Fi network saved for the next connection", "ssid", c.SSID)
		res.State = api.WifiApplied
		return res
	}
	if w.serverReachable(ctx) {
		err := w.retry(ctx, "wifi.confirm")
		if err == nil {
			d.Log.Info("Wi-Fi changed; MeteorCloud is reachable on the new network", "ssid", c.SSID)
			res.State = api.WifiApplied
			return res
		}
		// Unconfirmed, the change would be undone at the next start anyway:
		// undo it now rather than report it applied.
		d.Log.Error("could not confirm the Wi-Fi change; putting the old settings back", "err", err)
	} else {
		d.Log.Warn("Wi-Fi change not kept: MeteorCloud was unreachable on the new network", "ssid", c.SSID)
	}
	if err := w.rollback(ctx); err != nil {
		// Still pending: recover retries it and reports rolled_back then.
		d.Log.Error("roll back the Wi-Fi change; trying again later", "err", err)
		return fail(api.WifiFailed, fmt.Errorf("the old Wi-Fi settings could not be put back yet; the device keeps trying: %w", err))
	}
	res.State, res.Error = api.WifiRolledBack, errUnreachable
	return res
}

// retry calls a helper operation a few times (the helper may be restarting).
func (w *wifi) retry(ctx context.Context, op string) error {
	var err error
	for i := 0; i < helperTries; i++ {
		if err = w.helper.Call(ctx, op, nil, nil); err == nil || !sleep(ctx, helperRetry) {
			return err
		}
	}
	return err
}

// rollback puts the old settings back; nil only when the helper confirmed it.
func (w *wifi) rollback(ctx context.Context) error { return w.retry(ctx, "wifi.rollback") }

// serverReachable asks GET /api/v1/health until it answers or verifyFor
// passed. Neither a request nor a pause runs past that deadline, so only an
// answer within the window keeps the change.
func (w *wifi) serverReachable(ctx context.Context) bool {
	vctx, cancel := context.WithTimeout(ctx, w.verifyFor)
	defer cancel()
	for {
		hctx, hcancel := context.WithTimeout(vctx, healthCall)
		err := w.d.API.Health(hctx)
		hcancel()
		if err == nil && vctx.Err() == nil {
			return true
		}
		if !sleep(vctx, w.verifyPause) {
			return false
		}
	}
}

// status is the device's Wi-Fi state in the report's shape. Without the helper
// it still says why Wi-Fi cannot be managed, with the uplink the agent reads.
func (w *wifi) status(ctx context.Context) map[string]json.RawMessage {
	var answer map[string]json.RawMessage
	err := w.helper.Call(ctx, "wifi.status", nil, &answer)
	out := map[string]json.RawMessage{}
	if err != nil {
		reason := err.Error()
		var e *localrpc.Error
		if errors.As(err, &e) {
			reason = e.Message
		}
		out["supported"] = json.RawMessage("false")
		out["unsupported_reason"], _ = json.Marshal(cut(reason, 255))
		answer = nil
	}
	for _, k := range reportKeys {
		if v, ok := answer[k]; ok {
			out[k] = v
		}
	}
	if _, ok := out["uplink_interface"]; !ok && w.d.Host != nil {
		if up := w.d.Host.Uplink(); up.Interface != "" {
			out["uplink_type"], _ = json.Marshal(up.Type)
			out["uplink_interface"], _ = json.Marshal(up.Interface)
		}
	}
	return out
}

// report sends the waiting results and, when it changed, is stale or force is
// set, the state.
func (w *wifi) report(ctx context.Context, force bool) {
	d := w.d
	token, err := d.Identity.Token()
	if err != nil {
		return
	}
	pending, err := d.Outbox.Pending(KindWifiResult)
	if err != nil {
		d.Log.Error("read Wi-Fi results", "err", err)
	}
	if len(pending) > api.MaxWifiResults {
		pending = pending[:api.MaxWifiResults]
	}
	results := make([]api.WifiResult, 0, len(pending))
	ids := make([]string, 0, len(pending))
	for _, rep := range pending {
		var r api.WifiResult
		if json.Unmarshal(rep.Body, &r) != nil {
			_ = d.Outbox.Ack(rep.ID)
			continue
		}
		results, ids = append(results, r), append(ids, rep.ID)
	}
	st, _ := json.Marshal(w.status(ctx)) // map keys come out sorted, so equal states compare equal
	sendStatus := force || w.lastAt.IsZero() || time.Since(w.lastAt) >= statusRefresh || string(st) != string(w.lastStatus)
	if !sendStatus && len(results) == 0 {
		return
	}
	var body json.RawMessage
	if sendStatus {
		body = st
	}
	err = d.API.ReportWifi(ctx, token, results, body)
	switch {
	case api.IsAuth(err):
		d.Identity.AuthFailed(token)
		return
	case api.StatusOf(err) == 404:
		return
	case err != nil:
		d.Log.Warn("Wi-Fi report failed; trying again later", "err", err)
		return
	}
	if len(ids) > 0 {
		if err := d.Outbox.Ack(ids...); err != nil {
			d.Log.Error("remove reported Wi-Fi results", "err", err)
		}
	}
	if sendStatus {
		w.lastStatus, w.lastAt = st, time.Now()
	}
}
