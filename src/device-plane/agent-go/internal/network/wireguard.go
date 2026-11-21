package network

import (
	"context"
	"errors"
	"math/rand/v2"
	"net"
	"net/netip"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/helper"
)

// The wireguard module registers the device's WireGuard public key (the private
// key never leaves the device) and SSH facts, brings wg0 up through the helper
// and keeps the SSH lines the console's terminal needs in the SSH user's
// authorized_keys. Registration is a refresh, not a keepalive: between calls
// the tunnel runs from the last settings, also while the server is down.

const (
	wgName = "wireguard"
	// keepEvery is how often the tunnel is checked between registrations.
	keepEvery = time.Minute
	// registerEvery spaces registrations (×0.8–1.2).
	registerEvery = 15 * time.Minute
	// Failed registrations back off from retryMin to retryMax (×0.5–1.0).
	retryMin = 30 * time.Second
	retryMax = 30 * time.Minute
	// blockedFor is how long to wait after the server refused the device
	// (an operator has to act).
	blockedFor = 30 * time.Minute
	// noToolsFor is how long to wait before looking for wg again.
	noToolsFor = 10 * time.Minute
	// staleHandshake: WireGuard handshakes every 2 minutes while traffic flows.
	staleHandshake = 180 * time.Second
	// tunnelState is the state file with the last tunnel settings (no private key).
	tunnelState   = "wireguard.json"
	tunnelVersion = 1
)

// Server error codes that stop the tunnel until an operator acts.
const (
	codeDisabled   = "connectivity_disabled"
	codeRevoked    = "connectivity_revoked"
	codeKeyChanged = "wireguard_key_changed"
)

// sshHostKeys are read in this order; the first one found is reported.
var sshHostKeys = []string{"/etc/ssh/ssh_host_ed25519_key.pub", "/etc/ssh/ssh_host_ecdsa_key.pub", "/etc/ssh/ssh_host_rsa_key.pub"}

// WireGuard runs the device tunnel.
type WireGuard struct{}

// Name implements agent.Module.
func (m *WireGuard) Name() string { return wgName }

// Critical implements agent.Module.
func (m *WireGuard) Critical() bool { return false }

// tunnel is the last settings from the server, kept in state (no private key).
type tunnel struct {
	Address         string `json:"address"`
	ServerPublicKey string `json:"server_public_key"`
	Endpoint        string `json:"endpoint"`
	AllowedIPs      string `json:"allowed_ips"`
	Keepalive       int    `json:"keepalive"`
}

// Run implements agent.Module.
func (m *WireGuard) Run(ctx context.Context, d agent.Deps) error {
	again := d.Signals.Subscribe(func(s agent.Signal) bool {
		switch s.(type) {
		case agent.Enrolled, agent.CredentialsRotated, agent.ConfigChanged:
			return true
		}
		return false
	})
	defer again.Close()
	w := &wg{d: d, helper: noHelper{}, next: time.Now(), rand: rand.Float64}
	if d.Helper != nil {
		w.helper = d.Helper
	}
	for {
		d.Health.Tick(wgName)
		wait := w.cycle(ctx)
		t := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			t.Stop()
			return nil
		case <-again.C():
			again.Take()
			w.next, w.failures, w.blocked = time.Now(), 0, ""
		case <-t.C:
		}
		t.Stop()
	}
}

// wg is one Run's state: when to register next and why it is blocked.
type wg struct {
	d        agent.Deps
	helper   caller
	next     time.Time
	failures int
	blocked  string
	rand     func() float64
}

func (w *wg) jitter(d time.Duration, lo, hi float64) time.Duration {
	return time.Duration(float64(d) * (lo + (hi-lo)*w.rand()))
}

// toolsInstalled says wg and wg-quick exist.
func (w *wg) toolsInstalled() bool {
	if w.d.Host == nil {
		return false
	}
	for _, tool := range []string{"wg", "wg-quick"} {
		if _, err := w.d.Host.LookPath(tool); err != nil {
			return false
		}
	}
	return true
}

// cycle registers when due, otherwise keeps the tunnel up, and returns how
// long to wait.
func (w *wg) cycle(ctx context.Context) time.Duration {
	d := w.d
	if !w.toolsInstalled() {
		d.Health.Set(wgName, agent.StateWaiting, errors.New("WireGuard tools not installed (need wg and wg-quick: apt install wireguard-tools)"))
		return noToolsFor
	}
	if time.Now().Before(w.next) {
		if w.blocked == "" {
			w.keepUp(ctx)
		}
		return min(keepEvery, time.Until(w.next))
	}
	w.register(ctx)
	return min(keepEvery, max(time.Until(w.next), time.Second))
}

func (w *wg) register(ctx context.Context) {
	d := w.d
	token, err := d.Identity.Token()
	if err != nil {
		w.next = time.Now().Add(keepEvery)
		return
	}
	priv, pub, err := ensureKeypair(d.Paths.WireGuardDir())
	if err != nil {
		d.Health.Set(wgName, agent.StateFailed, err)
		d.Log.Error("WireGuard key pair", "err", err)
		w.failed(ctx, err)
		return
	}
	reported := w.trustedCA(ctx, helper.SSHKeysRequest{})
	req := api.WireGuardRequest{PublicKey: pub, SSHUser: w.sshUser(), SSHHostKey: w.hostKey(), SSHCAPublicKey: reported}
	resp, err := d.API.RegisterWireGuard(ctx, token, req)
	if err != nil {
		w.refused(ctx, token, err)
		return
	}
	t := tunnel{Address: resp.WGAddress, ServerPublicKey: resp.ServerPublicKey, Endpoint: resp.ServerEndpoint,
		AllowedIPs: resp.AllowedIPs, Keepalive: resp.PersistentKeepalive}
	if t.AllowedIPs == "" {
		t.AllowedIPs = "10.8.0.1/32"
	}
	if t.Keepalive == 0 {
		t.Keepalive = 25
	}
	if !strings.Contains(t.Address, "/") && t.Address != "" {
		t.Address += "/32"
	}
	if err := d.State.Write(tunnelState, tunnelVersion, t); err != nil {
		d.Log.Error("keep the WireGuard settings", "err", err)
	}
	w.up(ctx, t, priv)
	keys := helper.SSHKeysRequest{GatewayKeyRetired: resp.SSHGatewayKeyRetired}
	if resp.SSHGatewayPublicKey != nil {
		keys.GatewayKey = *resp.SSHGatewayPublicKey
	}
	if resp.SSHCAPublicKey != nil {
		keys.CAKey = *resp.SSHCAPublicKey
	}
	if resp.SSHPrincipal != nil {
		keys.Principal = *resp.SSHPrincipal
	}
	trusted := reported
	if keys != (helper.SSHKeysRequest{}) {
		trusted = w.trustedCA(ctx, keys)
	}
	if trusted != "" && trusted != reported {
		// Tell the server now that this device accepts certificates, not at the next refresh.
		req.SSHCAPublicKey = trusted
		if _, err := d.API.RegisterWireGuard(ctx, token, req); err != nil {
			d.Log.Info("could not report SSH certificate support yet; will with the next refresh", "err", err)
		}
	}
	w.failures, w.blocked = 0, ""
	w.next = time.Now().Add(w.jitter(registerEvery, 0.8, 1.2))
	d.Health.Set(wgName, agent.StateRunning, nil)
}

// refused handles a failed registration.
func (w *wg) refused(ctx context.Context, token string, err error) {
	d := w.d
	switch code := codeOf(err); {
	case api.IsAuth(err) && code != codeRevoked && code != codeKeyChanged:
		d.Identity.AuthFailed(token)
		w.next = time.Now().Add(keepEvery)
	case code == codeDisabled:
		if w.blocked != code {
			d.Log.Info("device connectivity is disabled on the server; WireGuard stays off")
		}
		w.block(code)
	case code == codeRevoked || code == codeKeyChanged:
		if w.blocked != code {
			d.Log.Warn("WireGuard access refused; tunnel stopped until an operator enrolls this device again", "code", code, "err", err)
		}
		if herr := w.helper.Call(ctx, "wireguard.down", nil, nil); herr != nil {
			d.Log.Warn("stop the WireGuard tunnel", "err", herr)
		}
		w.block(code)
	default:
		wait := w.failed(ctx, err)
		d.Log.Warn("WireGuard registration failed; keeping the current tunnel", "err", err, "retry_in", wait.Round(time.Second))
	}
}

func (w *wg) block(code string) {
	w.failures, w.blocked = 0, code
	w.next = time.Now().Add(w.jitter(blockedFor, 0.8, 1.2))
	w.d.Health.Set(wgName, agent.StateWaiting, errors.New(code))
}

// failed schedules a retry with backoff and keeps the tunnel up meanwhile.
func (w *wg) failed(ctx context.Context, _ error) time.Duration {
	w.failures++
	step := retryMin << min(w.failures-1, 10)
	wait := w.jitter(min(step, retryMax), 0.5, 1.0)
	w.next = time.Now().Add(wait)
	w.keepUp(ctx)
	return wait
}

// keepUp brings wg0 up from the last settings and follows the server's DNS
// name, without calling the server: the tunnel comes back after a reboot or
// an outage even while the control plane is unreachable.
func (w *wg) keepUp(ctx context.Context) {
	var t tunnel
	if _, err := w.d.State.Read(tunnelState, &t); err != nil || t.Address == "" {
		return
	}
	priv, _, err := readKeypair(w.d.Paths.WireGuardDir())
	if err != nil {
		return
	}
	w.up(ctx, t, priv)
}

// up applies t (the helper does nothing when wg0 already runs it) and points
// the peer at the server's current address.
func (w *wg) up(ctx context.Context, t tunnel, priv string) {
	d := w.d
	cfg := helper.WireGuardConfig{Address: t.Address, PrivateKey: priv, PublicKey: t.ServerPublicKey,
		Endpoint: t.Endpoint, AllowedIPs: t.AllowedIPs, Keepalive: t.Keepalive}
	var reply helper.WireGuardApplyReply
	if err := w.helper.Call(ctx, "wireguard.apply", cfg, &reply); err != nil {
		d.Health.Set(wgName, agent.StateFailed, err)
		d.Log.Warn("could not bring the WireGuard tunnel up", "err", err)
		return
	}
	if reply.Changed {
		d.Log.Info("WireGuard tunnel up", "address", t.Address, "endpoint", t.Endpoint)
	}
	w.refreshEndpoint(ctx, t)
}

// refreshEndpoint looks a DNS endpoint up again when handshakes stopped:
// WireGuard resolves it only when the tunnel starts, so a server that moved
// to a new IP would be lost.
func (w *wg) refreshEndpoint(ctx context.Context, t tunnel) {
	i := strings.LastIndexByte(t.Endpoint, ':')
	if i <= 0 {
		return
	}
	host, port := t.Endpoint[:i], t.Endpoint[i+1:]
	if _, err := netip.ParseAddr(strings.Trim(host, "[]")); err == nil {
		return // a fixed IP: nothing to look up
	}
	var show helper.WireGuardShowReply
	if err := w.helper.Call(ctx, "wireguard.show", nil, &show); err != nil {
		return
	}
	current := ""
	for _, p := range show.Peers {
		if p.PublicKey != t.ServerPublicKey {
			continue
		}
		if p.LatestHandshake > 0 && time.Since(time.Unix(p.LatestHandshake, 0)) < staleHandshake {
			return
		}
		current = p.Endpoint
	}
	rctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupNetIP(rctx, "ip4", host)
	if err != nil || len(ips) == 0 {
		w.d.Log.Warn("could not resolve the WireGuard server", "host", host, "err", err)
		return
	}
	target := net.JoinHostPort(ips[0].Unmap().String(), port)
	if target == current {
		return
	}
	if err := w.helper.Call(ctx, "wireguard.endpoint", helper.WireGuardEndpointRequest{PublicKey: t.ServerPublicKey, Endpoint: target}, nil); err != nil {
		w.d.Log.Warn("update the WireGuard server address", "err", err)
		return
	}
	w.d.Log.Info("WireGuard server moved", "host", host, "now", target, "was", current)
}

// trustedCA keeps the SSH lines current (an empty request changes nothing)
// and returns the CA the SSH user trusts, "" when unknown.
func (w *wg) trustedCA(ctx context.Context, r helper.SSHKeysRequest) string {
	var reply helper.SSHKeysReply
	if err := w.helper.Call(ctx, "ssh.authorized_keys", r, &reply); err != nil {
		if r != (helper.SSHKeysRequest{}) {
			w.d.Log.Warn("could not update authorized_keys for the browser terminal", "err", err)
		}
		return ""
	}
	return reply.TrustedCA
}

// sshUser is the account the browser terminal signs in as (config.json).
func (w *wg) sshUser() string {
	c, err := config.Load(w.d.Paths.ConfigFile)
	if err != nil || !sshUserRe(c.SSHUser) {
		return ""
	}
	return c.SSHUser
}

func sshUserRe(s string) bool {
	if s == "" || len(s) > 32 {
		return false
	}
	for i, r := range s {
		ok := r == '_' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || (i > 0 && r == '-')
		if !ok {
			return false
		}
	}
	return true
}

// hostKey is this device's sshd host public key ("<type> <base64>"), so the
// server can pin it.
func (w *wg) hostKey() string {
	for _, p := range sshHostKeys {
		if key := readHostKey(w.d.Paths.Root + p); key != "" {
			return key
		}
	}
	return ""
}
