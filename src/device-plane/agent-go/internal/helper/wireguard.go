package helper

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"meteorcloud/meteorcli/internal/localrpc"
)

// WireGuard on wg0: the Python meteorcli-wg helper. The agent sends the tunnel
// settings as typed fields and the helper writes the config itself, with only
// the keys meteorcli uses (no PreUp/PostUp hooks, Table, DNS, SaveConfig ...).
// Address and AllowedIPs must be single private hosts that are not on a local
// network, so the agent cannot route the host's traffic (or a LAN neighbour)
// into a tunnel it chose. The private key is in the request, written only to a
// 0600 file in the helper's own state directory, and never logged.

// wgInterface is the only interface the helper manages.
const wgInterface = "wg0"

// WireGuardConfig is the params of wireguard.apply. PrivateKey is a secret.
type WireGuardConfig struct {
	Address    string `json:"address"`
	PrivateKey string `json:"private_key"`
	PublicKey  string `json:"public_key"` // the server's
	Endpoint   string `json:"endpoint"`
	AllowedIPs string `json:"allowed_ips"`
	Keepalive  int    `json:"keepalive"`
}

// WireGuardApplyReply says whether wg0 was (re)started.
type WireGuardApplyReply struct {
	Changed bool `json:"changed"`
}

// WireGuardPeer is one peer of wireguard.show.
type WireGuardPeer struct {
	PublicKey string `json:"public_key"`
	// LatestHandshake is a Unix time, 0 before the first handshake.
	LatestHandshake int64  `json:"latest_handshake"`
	Endpoint        string `json:"endpoint"`
}

// WireGuardShowReply is the answer of wireguard.show.
type WireGuardShowReply struct {
	Up    bool            `json:"up"`
	Peers []WireGuardPeer `json:"peers"`
}

// WireGuardEndpointRequest points the server peer at a new address (the
// agent resolved the server's name again).
type WireGuardEndpointRequest struct {
	PublicKey string `json:"public_key"`
	Endpoint  string `json:"endpoint"`
}

func wgKey(v string) error {
	raw, err := base64.StdEncoding.DecodeString(v)
	if len(v) != 44 || err != nil || len(raw) != 32 {
		return badRequest("invalid WireGuard key")
	}
	return nil
}

// wgEndpoint accepts ip:port, [ipv6]:port, or a plain DNS name with a port.
// literalOnly refuses names.
func wgEndpoint(v string, literalOnly bool) error {
	i := strings.LastIndexByte(v, ':')
	if i <= 0 {
		return badRequest("invalid endpoint")
	}
	host, port := v[:i], v[i+1:]
	if n, err := strconv.Atoi(port); err != nil || n < 1 || n > 65535 || strconv.Itoa(n) != port {
		return badRequest("invalid endpoint")
	}
	if _, err := netip.ParseAddr(strings.Trim(host, "[]")); err == nil {
		return nil
	}
	if literalOnly {
		return badRequest("endpoint must be an IP address and port")
	}
	if strings.HasPrefix(host, "-") || strings.IndexFunc(host, func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '.')
	}) >= 0 {
		return badRequest("invalid endpoint")
	}
	return nil
}

// wgHosts checks a comma-separated list of single private hosts and returns it
// canonical ("a/32, b/32").
func wgHosts(v string) (string, []netip.Addr, error) {
	var items []string
	var addrs []netip.Addr
	for _, item := range strings.Split(v, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		p, err := netip.ParsePrefix(item)
		if err != nil {
			a, aerr := netip.ParseAddr(item)
			if aerr != nil {
				return "", nil, badRequest("invalid address %q", item)
			}
			p = netip.PrefixFrom(a, a.BitLen())
		}
		if p.Bits() != p.Addr().BitLen() || !p.Addr().IsPrivate() {
			return "", nil, badRequest("address must be a single private host: %q", item)
		}
		items = append(items, p.String())
		addrs = append(addrs, p.Addr())
	}
	if len(items) == 0 {
		return "", nil, badRequest("empty address list")
	}
	return strings.Join(items, ", "), addrs, nil
}

// render validates c and returns the config file and the tunnel's hosts.
func (c WireGuardConfig) render() (string, []netip.Addr, error) {
	address, hosts, err := wgHosts(c.Address)
	if err != nil {
		return "", nil, err
	}
	allowed, more, err := wgHosts(c.AllowedIPs)
	if err != nil {
		return "", nil, err
	}
	if err := wgKey(c.PrivateKey); err != nil {
		return "", nil, err
	}
	if err := wgKey(c.PublicKey); err != nil {
		return "", nil, err
	}
	if err := wgEndpoint(c.Endpoint, false); err != nil {
		return "", nil, err
	}
	if c.Keepalive < 0 || c.Keepalive > 65535 {
		return "", nil, badRequest("invalid keepalive")
	}
	conf := "[Interface]\nAddress = " + address + "\nPrivateKey = " + c.PrivateKey +
		"\n\n[Peer]\nPublicKey = " + c.PublicKey + "\nEndpoint = " + c.Endpoint +
		"\nAllowedIPs = " + allowed + "\nPersistentKeepalive = " + strconv.Itoa(c.Keepalive) + "\n"
	return conf, append(hosts, more...), nil
}

func (h Host) wgConf() string { return filepath.Join(h.StateDir, "wireguard", wgInterface+".conf") }

func (h Host) wgUp() bool {
	dir := h.SysNetDir
	if dir == "" {
		dir = "/sys/class/net"
	}
	_, err := os.Stat(filepath.Join(dir, wgInterface))
	return err == nil
}

// wgTearDown removes wg0 (and the routes wg-quick added with it). An absent
// wg0 is a success; one still there afterwards is an error.
func (h Host) wgTearDown(ctx context.Context) error {
	if !h.wgUp() {
		return nil
	}
	if _, err := os.Stat(h.wgConf()); err == nil {
		if _, err := h.Run(ctx, "wg-quick", "down", h.wgConf()); err == nil && !h.wgUp() {
			return nil
		}
	}
	_, err := h.Run(ctx, "ip", "link", "delete", "dev", wgInterface)
	if h.wgUp() {
		return fmt.Errorf("wg0 is still up: %v", err)
	}
	return nil
}

// refuseLocalHosts fails when a tunnel host is reachable without a gateway:
// it is on a local network, and taking it over would hijack that link.
func (h Host) refuseLocalHosts(ctx context.Context, hosts []netip.Addr) error {
	// Linux answers `ip route get` for an address this host already has with
	// "local … dev lo". That includes the address of the tunnel we manage, so
	// a new server endpoint must still be allowed to replace it.
	own := h.tunnelAddresses(ctx)
	for _, a := range hosts {
		out, err := h.Run(ctx, "ip", "-j", "route", "get", a.String())
		var routes []struct {
			Dev     string `json:"dev"`
			Gateway string `json:"gateway"`
		}
		// No usable answer means the check cannot be made: refuse rather
		// than risk taking over a local link.
		if err != nil || json.Unmarshal(bytes.TrimSpace(out), &routes) != nil || len(routes) == 0 {
			return fmt.Errorf("cannot check the route to %s: %v", a, err)
		}
		for _, r := range routes {
			if r.Dev == "lo" {
				if _, ok := own[a]; ok {
					continue
				}
			}
			if r.Dev != wgInterface && r.Gateway == "" {
				return badRequest("%s is on the local network (%s)", a, r.Dev)
			}
		}
	}
	return nil
}

// tunnelAddresses is the set of IPv4 addresses currently on wg0. Empty when
// the interface is down or its addresses cannot be read.
func (h Host) tunnelAddresses(ctx context.Context) map[netip.Addr]struct{} {
	own := map[netip.Addr]struct{}{}
	if !h.wgUp() {
		return own
	}
	out, err := h.Run(ctx, "ip", "-j", "-4", "addr", "show", "dev", wgInterface)
	if err != nil {
		return own
	}
	var links []struct {
		AddrInfo []struct {
			Local string `json:"local"`
		} `json:"addr_info"`
	}
	if json.Unmarshal(bytes.TrimSpace(out), &links) != nil {
		return own
	}
	for _, link := range links {
		for _, info := range link.AddrInfo {
			if a, err := netip.ParseAddr(info.Local); err == nil {
				own[a] = struct{}{}
			}
		}
	}
	return own
}

func (h Host) wireguardApply(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var c WireGuardConfig
	if err := decode(raw, &c); err != nil {
		return nil, err
	}
	conf, hosts, err := c.render()
	if err != nil {
		return nil, err
	}
	old, oldErr := os.ReadFile(h.wgConf())
	wasUp := h.wgUp()
	if oldErr == nil && string(old) == conf && wasUp {
		return WireGuardApplyReply{}, nil
	}
	// Checked first with the working tunnel still up (hosts routed through
	// wg0 are checked again below), so a bad config usually never touches it.
	if err := h.refuseLocalHosts(ctx, hosts); err != nil {
		return nil, err
	}
	// restore brings the previous working tunnel back after a failed change.
	restore := func(cause error) error {
		if oldErr != nil || !wasUp {
			return cause
		}
		_ = h.wgTearDown(ctx)
		_ = os.Remove(h.wgConf())
		if err := writePrivate(h.wgConf(), old); err != nil {
			return fmt.Errorf("%w (the previous tunnel could not be restored: %v)", cause, err)
		}
		if _, err := h.Run(ctx, "wg-quick", "up", h.wgConf()); err != nil {
			return fmt.Errorf("%w (the previous tunnel could not be restored: %v)", cause, err)
		}
		return cause
	}
	if err := h.wgTearDown(ctx); err != nil {
		return nil, err
	}
	// Again after the old wg0 is gone, so its own routes do not hide a local host.
	if err := h.refuseLocalHosts(ctx, hosts); err != nil {
		return nil, restore(err)
	}
	if err := os.MkdirAll(filepath.Dir(h.wgConf()), 0o700); err != nil {
		return nil, restore(err)
	}
	_ = os.Remove(h.wgConf())
	if err := writePrivate(h.wgConf(), []byte(conf)); err != nil {
		return nil, restore(err)
	}
	if _, err := h.Run(ctx, "wg-quick", "up", h.wgConf()); err != nil {
		return nil, restore(err)
	}
	return WireGuardApplyReply{Changed: true}, nil
}

func (h Host) wireguardDown(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	if err := h.wgTearDown(ctx); err != nil {
		return nil, err
	}
	return map[string]bool{"down": true}, nil
}

func (h Host) wireguardShow(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	reply := WireGuardShowReply{Peers: []WireGuardPeer{}}
	if !h.wgUp() {
		return reply, nil
	}
	handshakes, err := h.Run(ctx, "wg", "show", wgInterface, "latest-handshakes")
	if err != nil {
		return reply, nil // not a WireGuard interface, or wg missing
	}
	reply.Up = true
	endpoints, _ := h.Run(ctx, "wg", "show", wgInterface, "endpoints")
	ends := peerColumn(endpoints)
	for _, line := range strings.Split(strings.TrimSpace(string(handshakes)), "\n") {
		key, value, ok := strings.Cut(line, "\t")
		if !ok {
			continue
		}
		t, _ := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
		end := ends[key]
		if end == "(none)" {
			end = ""
		}
		reply.Peers = append(reply.Peers, WireGuardPeer{PublicKey: key, LatestHandshake: t, Endpoint: end})
	}
	return reply, nil
}

// peerColumn parses `wg show <if> <field>` ("<public key>\t<value>" lines).
func peerColumn(out []byte) map[string]string {
	m := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if key, value, ok := strings.Cut(line, "\t"); ok {
			m[key] = strings.TrimSpace(value)
		}
	}
	return m
}

func (h Host) wireguardEndpoint(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WireGuardEndpointRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := wgKey(r.PublicKey); err != nil {
		return nil, err
	}
	if err := wgEndpoint(r.Endpoint, true); err != nil {
		return nil, err
	}
	if _, err := h.Run(ctx, "wg", "set", wgInterface, "peer", r.PublicKey, "endpoint", r.Endpoint); err != nil {
		return nil, err
	}
	return map[string]bool{"changed": true}, nil
}

// auditWireGuard names the tunnel, never the private key.
func auditWireGuard(raw json.RawMessage) []any {
	var c WireGuardConfig
	_ = json.Unmarshal(raw, &c)
	return []any{"address", c.Address, "endpoint", c.Endpoint}
}
