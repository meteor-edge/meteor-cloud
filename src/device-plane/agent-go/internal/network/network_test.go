package network

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
	"meteorcloud/meteorcli/internal/state"
)

// fakeHelper answers helper operations from a table and records them.
type fakeHelper struct {
	mu      sync.Mutex
	calls   []string
	params  map[string][]json.RawMessage
	answers map[string]any
	errs    map[string]error
	// answer, when set, answers instead of answers (it sees the params).
	answer func(op string, params json.RawMessage) any
}

func newFakeHelper() *fakeHelper {
	return &fakeHelper{params: map[string][]json.RawMessage{}, answers: map[string]any{}, errs: map[string]error{}}
}

func (f *fakeHelper) Call(_ context.Context, op string, params, result any) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, op)
	raw, _ := json.Marshal(params)
	f.params[op] = append(f.params[op], raw)
	if err := f.errs[op]; err != nil {
		return err
	}
	a := f.answers[op]
	if f.answer != nil {
		if v := f.answer(op, raw); v != nil {
			a = v
		}
	}
	if result != nil && a != nil {
		data, _ := json.Marshal(a)
		return json.Unmarshal(data, result)
	}
	return nil
}

func (f *fakeHelper) count(op string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.params[op])
}

func deps(t *testing.T, cp *fakecp.Server) agent.Deps {
	d := agenttest.Deps(t, cp, nil, nil)
	s, err := state.NewStore(d.Paths.StateDir)
	if err != nil {
		t.Fatal(err)
	}
	d.State = s
	return d
}

func rev(n int) *int { return &n }

func TestWifiApplyOutcomes(t *testing.T) {
	cp := fakecp.New(t)
	d := deps(t, cp)
	h := newFakeHelper()
	w := &wifi{d: d, helper: h, verifyFor: 20 * time.Millisecond, verifyPause: 5 * time.Millisecond}
	ctx := context.Background()
	pw := "long-password"

	// Saved for later.
	if r := w.apply(ctx, api.WifiChange{ID: "a", Revision: rev(1), SSID: "lab", Password: &pw}); r.State != api.WifiApplied {
		t.Fatalf("saved = %+v", r)
	}
	var req helper.WifiApplyRequest
	_ = json.Unmarshal(h.params["wifi.apply"][0], &req)
	if req.Security != "wpa-psk" || req.Password != pw {
		t.Errorf("request %+v", req)
	}
	// Connect now, the server answers: confirmed.
	cp.On("GET", "/api/v1/health", fakecp.OK(map[string]string{"status": "ok"}))
	if r := w.apply(ctx, api.WifiChange{ID: "b", SSID: "lab", Password: &pw, ConnectNow: true}); r.State != api.WifiApplied || h.count("wifi.confirm") != 1 {
		t.Fatalf("connect now = %+v %v", r, h.calls)
	}
	// The server does not answer: rolled back.
	cp.On("GET", "/api/v1/health", fakecp.Fail(503, "down"))
	if r := w.apply(ctx, api.WifiChange{ID: "c", SSID: "lab", Password: &pw, ConnectNow: true}); r.State != api.WifiRolledBack ||
		r.Error != errUnreachable || h.count("wifi.rollback") != 1 {
		t.Fatalf("unreachable = %+v %v", r, h.calls)
	}
	// The network cannot be joined.
	h.errs["wifi.apply"] = localrpc.Errorf(helper.CodeNotJoined, "could not join lab")
	if r := w.apply(ctx, api.WifiChange{ID: "d", SSID: "lab", Password: &pw, ConnectNow: true}); r.State != api.WifiRolledBack || r.Error != "could not join lab" {
		t.Fatalf("not joined = %+v", r)
	}
	h.errs["wifi.apply"] = localrpc.Errorf(localrpc.CodeBadRequest, "bad ssid")
	if r := w.apply(ctx, api.WifiChange{ID: "e", SSID: "lab"}); r.State != api.WifiFailed {
		t.Fatalf("bad = %+v", r)
	}
	// Removal.
	if r := w.apply(ctx, api.WifiChange{ID: "f", Action: "remove", SSID: "lab"}); r.State != api.WifiRemoved {
		t.Fatalf("remove = %+v", r)
	}
	h.errs["wifi.remove"] = localrpc.Errorf(helper.CodeWouldCutOff, "only link")
	if r := w.apply(ctx, api.WifiChange{ID: "g", Action: "remove", SSID: "lab"}); r.State != api.WifiFailed || r.Error != "only link" {
		t.Fatalf("remove refused = %+v", r)
	}
}

func TestWifiCollectAndReport(t *testing.T) {
	cp := fakecp.New(t)
	d := deps(t, cp)
	h := newFakeHelper()
	h.answers["wifi.status"] = map[string]any{"supported": true, "connected_ssid": "home", "interface": "wlan0",
		"uplink_type": "wifi", "uplink_interface": "wlan0", "pending": nil, "extra": "dropped"}
	w := &wifi{d: d, helper: h, verifyFor: time.Millisecond, verifyPause: time.Millisecond}
	ctx := context.Background()
	pw := "long-password"
	cp.On("GET", "/api/v1/agent/network/wifi", fakecp.OK(map[string]any{"changes": []api.WifiChange{
		{ID: "a", Revision: rev(2), SSID: "lab", Password: &pw}, {ID: "b", Revision: rev(3), Action: "remove", SSID: "old"}}}))
	cp.On("POST", "/api/v1/agent/network/wifi/report", fakecp.OK(map[string]any{"accepted": true}))

	w.collect(ctx)
	w.report(ctx, false)
	calls := cp.Calls("POST", "/api/v1/agent/network/wifi/report")
	if len(calls) != 1 {
		t.Fatalf("%d reports", len(calls))
	}
	body := calls[0].Body
	results := body["results"].([]any)
	if len(results) != 2 || results[0].(map[string]any)["state"] != "applied" || results[1].(map[string]any)["state"] != "removed" {
		t.Errorf("results %v", results)
	}
	st := body["status"].(map[string]any)
	if st["connected_ssid"] != "home" || st["extra"] != nil || st["uplink_interface"] != "wlan0" {
		t.Errorf("status %v", st)
	}
	if strings.Contains(string(mustJSON(body)), pw) {
		t.Fatal("the password was reported")
	}
	// Nothing new: no report. Forced: the state again, no results.
	w.report(ctx, false)
	if n := len(cp.Calls("POST", "/api/v1/agent/network/wifi/report")); n != 1 {
		t.Errorf("unchanged state reported (%d)", n)
	}
	w.report(ctx, true)
	calls = cp.Calls("POST", "/api/v1/agent/network/wifi/report")
	if len(calls) != 2 || len(calls[1].Body["results"].([]any)) != 0 {
		t.Errorf("forced report %v", calls[len(calls)-1].Body)
	}
	// A failed report keeps the results for later.
	cp.On("POST", "/api/v1/agent/network/wifi/report", fakecp.Fail(503, "down"))
	w.keep(api.WifiResult{ID: "c", Revision: rev(4), State: api.WifiFailed, Error: "x"})
	w.report(ctx, false)
	if p, _ := d.Outbox.Pending(KindWifiResult); len(p) != 1 {
		t.Errorf("pending after a failed report: %d", len(p))
	}
}

func TestWifiStatusWithoutHelper(t *testing.T) {
	d := deps(t, fakecp.New(t))
	w := &wifi{d: d, helper: noHelper{}}
	st := w.status(context.Background())
	if string(st["supported"]) != "false" || !strings.Contains(string(st["unsupported_reason"]), "helper") {
		t.Errorf("status %v", st)
	}
}

func TestWifiRecover(t *testing.T) {
	d := deps(t, fakecp.New(t))
	h := newFakeHelper()
	h.answers["wifi.status"] = map[string]any{"pending": map[string]any{"id": "a", "revision": 5}}
	w := &wifi{d: d, helper: h}
	w.recover(context.Background())
	p, _ := d.Outbox.Pending(KindWifiResult)
	if h.count("wifi.rollback") != 1 || len(p) != 1 || !strings.Contains(string(p[0].Body), errInterrupted) {
		t.Fatalf("rollback %d, pending %v", h.count("wifi.rollback"), p)
	}
}

func TestWifiModuleCollectsOnSignal(t *testing.T) {
	cp := fakecp.New(t)
	d := deps(t, cp)
	cp.On("GET", "/api/v1/agent/network/wifi", fakecp.OK(map[string]any{"changes": []any{}}))
	cp.On("POST", "/api/v1/agent/network/wifi/report", fakecp.OK(map[string]any{"accepted": true}))
	agenttest.Start(t, &WiFi{firstStatus: time.Millisecond}, d)
	cp.Wait(t, "POST", "/api/v1/agent/network/wifi/report", 1, 5*time.Second)
	d.Signals.Publish(agent.WorkWaiting{Kind: "wifi"})
	cp.Wait(t, "GET", "/api/v1/agent/network/wifi", 1, 5*time.Second)
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

const serverKey = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

func registered(extra map[string]any) fakecp.Reply {
	body := map[string]any{"status": "active", "provider": "wireguard", "wg_address": "10.8.0.12",
		"server_public_key": serverKey, "server_endpoint": "192.0.2.1:51820", "allowed_ips": "10.8.0.1/32",
		"persistent_keepalive": 25, "ssh_user": "pi", "ssh_gateway_public_key": nil, "ssh_gateway_key_retired": false,
		"ssh_ca_public_key": nil, "ssh_principal": nil}
	for k, v := range extra {
		body[k] = v
	}
	return fakecp.OK(body)
}

func newWG(t *testing.T, cp *fakecp.Server) (*wg, *fakeHelper) {
	d := deps(t, cp)
	h := newFakeHelper()
	h.answers["wireguard.apply"] = helper.WireGuardApplyReply{Changed: true}
	if err := os.MkdirAll(filepath.Join(d.Paths.Root, "etc", "ssh"), 0o755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(d.Paths.Root, "etc", "ssh", "ssh_host_ed25519_key.pub"),
		[]byte("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHost root@pi\n"), 0o644)
	if err := (&config.Config{APIBase: cp.URL, SSHUser: "pi"}).Save(d.Paths.ConfigFile, config.Owner{}); err != nil {
		t.Fatal(err)
	}
	return &wg{d: d, helper: h, next: time.Now(), rand: func() float64 { return 0.5 }}, h
}

func TestWireGuardRegister(t *testing.T) {
	cp := fakecp.New(t)
	w, h := newWG(t, cp)
	ca := "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICa"
	cp.On("POST", "/api/v1/agent/connectivity/wireguard",
		registered(map[string]any{"ssh_ca_public_key": ca + " meteorcloud", "ssh_principal": "device-1"}), registered(nil))
	trusted := ""
	h.answer = func(op string, params json.RawMessage) any {
		if op != "ssh.authorized_keys" {
			return nil
		}
		if strings.Contains(string(params), "device-1") {
			trusted = ca
		}
		return helper.SSHKeysReply{User: "pi", TrustedCA: trusted}
	}
	w.register(context.Background())

	calls := cp.Calls("POST", "/api/v1/agent/connectivity/wireguard")
	if len(calls) != 2 {
		t.Fatalf("%d registrations (a newly trusted CA is reported at once)", len(calls))
	}
	first := calls[0].Body
	priv, pub, err := readKeypair(w.d.Paths.WireGuardDir())
	if err != nil || first["public_key"] != pub || first["ssh_user"] != "pi" ||
		first["ssh_host_key"] != "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHost" {
		t.Fatalf("request %v (%v)", first, err)
	}
	if calls[1].Body["ssh_ca_public_key"] != ca {
		t.Errorf("second request %v", calls[1].Body)
	}
	if strings.Contains(string(mustJSON(first)), priv) {
		t.Fatal("the private key was sent")
	}
	var cfg helper.WireGuardConfig
	_ = json.Unmarshal(h.params["wireguard.apply"][0], &cfg)
	if cfg.Address != "10.8.0.12/32" || cfg.PrivateKey != priv || cfg.PublicKey != serverKey || cfg.Keepalive != 25 {
		t.Errorf("config %+v", cfg)
	}
	var keys helper.SSHKeysRequest
	_ = json.Unmarshal(h.params["ssh.authorized_keys"][1], &keys)
	if keys.CAKey != ca+" meteorcloud" || keys.Principal != "device-1" {
		t.Errorf("keys %+v", keys)
	}
	var saved tunnel
	if v, err := w.d.State.Read(tunnelState, &saved); err != nil || v != tunnelVersion || saved.Endpoint != "192.0.2.1:51820" {
		t.Errorf("state %+v %d %v", saved, v, err)
	}
	if until := time.Until(w.next); until < 14*time.Minute || until > 16*time.Minute {
		t.Errorf("next registration in %v", until)
	}

	// The server is down: the tunnel stays up from the saved settings.
	cp.On("POST", "/api/v1/agent/connectivity/wireguard", fakecp.Fail(503, "down"))
	applies := h.count("wireguard.apply")
	w.register(context.Background())
	if h.count("wireguard.apply") != applies+1 || w.failures != 1 {
		t.Errorf("outage: applies %d failures %d", h.count("wireguard.apply")-applies, w.failures)
	}
	if until := time.Until(w.next); until < 10*time.Second || until > 31*time.Second {
		t.Errorf("retry in %v", until)
	}
}

func TestWireGuardRefusals(t *testing.T) {
	for _, c := range []struct {
		reply fakecp.Reply
		block string
		down  bool
	}{
		{fakecp.Fail(404, codeDisabled), codeDisabled, false},
		{fakecp.Fail(403, codeRevoked), codeRevoked, true},
		{fakecp.Fail(409, codeKeyChanged), codeKeyChanged, true},
	} {
		cp := fakecp.New(t)
		w, h := newWG(t, cp)
		cp.On("POST", "/api/v1/agent/connectivity/wireguard", c.reply)
		w.register(context.Background())
		if w.blocked != c.block || (h.count("wireguard.down") == 1) != c.down {
			t.Errorf("%s: blocked %q, down %d", c.block, w.blocked, h.count("wireguard.down"))
		}
		if until := time.Until(w.next); until < 25*time.Minute {
			t.Errorf("%s: next in %v", c.block, until)
		}
		if _, err := w.d.Identity.Token(); err != nil {
			t.Errorf("%s: the device token was dropped", c.block)
		}
	}
	cp := fakecp.New(t)
	w, _ := newWG(t, cp)
	cp.On("POST", "/api/v1/agent/connectivity/wireguard", fakecp.Fail(401, "invalid_token"))
	w.register(context.Background())
	if _, err := w.d.Identity.Token(); err == nil {
		t.Error("a refused device token stayed in use")
	}
}

func TestKeypair(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "wg")
	priv, pub, err := ensureKeypair(dir)
	if err != nil {
		t.Fatal(err)
	}
	if again, _, _ := ensureKeypair(dir); again != priv {
		t.Error("a second call made a new key")
	}
	if derived, _ := publicKeyOf(priv); derived != pub || len(pub) != 44 {
		t.Errorf("public %q derived %q", pub, derived)
	}
	st, _ := os.Stat(filepath.Join(dir, "private.key"))
	if st.Mode().Perm() != 0o600 {
		t.Errorf("mode %v", st.Mode())
	}
	// RFC 7748, section 6.1.
	if p, _ := publicKeyOf("dwdtCnMYpX08FsFyUbJmRd9ML4frwJkqsXf7pR25LCo="); p != "hSDwCYkwp1R0i33ctD73Wg2/Og0mOBr066SpjqqbTmo=" {
		t.Errorf("derived %q", p)
	}
}

func TestSSHUser(t *testing.T) {
	for s, ok := range map[string]bool{"pi": true, "deploy-1": true, "": false, "-x": false, "a b": false, strings.Repeat("a", 33): false} {
		if sshUserRe(s) != ok {
			t.Errorf("%q", s)
		}
	}
}
