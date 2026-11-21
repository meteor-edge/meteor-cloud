package helper

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
	"meteorcloud/meteorcli/internal/platform"
)

// scripted answers each tool run by the first matching prefix of its command
// line ("nmcli -t -f RUNNING general"), and records every run.
type scripted struct {
	mu      sync.Mutex
	calls   []string
	answers map[string]string
	fail    map[string]bool
}

func (s *scripted) run(_ context.Context, name string, args ...string) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	line := strings.Join(append([]string{name}, args...), " ")
	s.calls = append(s.calls, line)
	best := ""
	for prefix := range s.answers {
		if strings.HasPrefix(line, prefix) && len(prefix) > len(best) {
			best = prefix
		}
	}
	for prefix := range s.fail {
		if strings.HasPrefix(line, prefix) {
			return nil, errors.New(line + " failed")
		}
	}
	return []byte(s.answers[best]), nil
}

func (s *scripted) host(t *testing.T) Host {
	stream := func(ctx context.Context, w io.Writer, name string, args ...string) error {
		out, err := s.run(ctx, name, args...)
		if err != nil {
			return err
		}
		_, err = w.Write(out)
		return err
	}
	dir := t.TempDir()
	return Host{
		Run: s.run, Stream: stream, StreamAll: stream,
		StateDir: filepath.Join(dir, "state"), NMConnectionsDir: filepath.Join(dir, "nm"),
		SysNetDir: filepath.Join(dir, "net"), ConfigFile: filepath.Join(dir, "config.json"),
		SystemdDir: dir, DockerSocket: filepath.Join(dir, "docker.sock"),
		Uplink:      func() platform.Uplink { return platform.Uplink{Type: "ethernet", Interface: "eth0"} },
		LookNetplan: func() bool { return false },
		LookupUser:  func(string) (UserInfo, error) { return UserInfo{}, errNoUser },
	}
}

func (s *scripted) ran(prefix string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return slices.ContainsFunc(s.calls, func(c string) bool { return strings.HasPrefix(c, prefix) })
}

func TestSplitTerse(t *testing.T) {
	for in, want := range map[string][]string{
		`yes:my\:net:80`: {"yes", "my:net", "80"},
		`a\\b:`:          {`a\b`, ""},
		`plain`:          {"plain"},
	} {
		if got := splitTerse(in); !slices.Equal(got, want) {
			t.Errorf("splitTerse(%q) = %q", in, got)
		}
	}
}

func TestKeyfile(t *testing.T) {
	kf := wifiKeyfile(WifiApplyRequest{SSID: " lab;1 ", Security: "wpa-psk", Password: `pa\ss;word`, Hidden: true})
	for _, want := range []string{`ssid=\slab\;1\s`, `psk=pa\\ss\;word`, "hidden=true", "id=" + wifiProfileName(" lab;1 "),
		"autoconnect-priority=10", "key-mgmt=wpa-psk"} {
		if !strings.Contains(kf, want) {
			t.Errorf("keyfile lacks %q:\n%s", want, kf)
		}
	}
	if open := wifiKeyfile(WifiApplyRequest{SSID: "cafe", Security: "open"}); strings.Contains(open, "wifi-security") {
		t.Errorf("open network with a security section:\n%s", open)
	}
}

func nmHost(t *testing.T) (*scripted, Host) {
	s := &scripted{answers: map[string]string{
		"nmcli -t -f RUNNING general":                     "running\n",
		"nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device": "eth0:ethernet:connected:Wired\nwlan0:wifi:connected:home\n",
		"nmcli -t -f ACTIVE,SSID,SIGNAL device wifi list": "no:other:40\nyes:home:72\n",
		"nmcli -t -f NAME,UUID,TYPE,FILENAME connection show": "home:u1:802-11-wireless:/x/home.nmconnection\n" +
			"Wired:u2:802-3-ethernet:/x/w.nmconnection\n",
		"nmcli -e no -g 802-11-wireless.ssid connection show uuid u1": "home\n",
		"nmcli -t -f UUID,TYPE connection show --active":              "u1:802-11-wireless\n",
	}, fail: map[string]bool{}}
	return s, s.host(t)
}

func TestWifiStatus(t *testing.T) {
	_, h := nmHost(t)
	out, err := call(t, h, "wifi.status", nil)
	if err != nil {
		t.Fatal(err)
	}
	var st map[string]any
	_ = json.Unmarshal(out, &st)
	if st["supported"] != true || st["connected_ssid"] != "home" || st["signal_percent"] != 72.0 || st["interface"] != "wlan0" ||
		st["wired_connected"] != true || st["uplink_interface"] != "eth0" || st["pending"] != nil {
		t.Fatalf("status %s", out)
	}
	if nets := st["saved_networks"].([]any); len(nets) != 1 || nets[0].(map[string]any)["managed"] != false {
		t.Errorf("saved %v", nets)
	}

	s, h := nmHost(t)
	s.answers["nmcli -t -f RUNNING general"] = "stopped\n"
	out, _ = call(t, h, "wifi.status", nil)
	if !strings.Contains(string(out), `"supported":false`) || !strings.Contains(string(out), "not running") ||
		!strings.Contains(string(out), `"uplink_type":"ethernet"`) {
		t.Errorf("unsupported status %s", out)
	}
}

func TestWifiApplyConnectNowConfirm(t *testing.T) {
	s, h := nmHost(t)
	rev := 3
	req := WifiApplyRequest{ID: "8a1c2d3e-0000-4000-8000-000000000001", Revision: &rev, SSID: "lab", Password: "secret-pass", ConnectNow: true}
	out, err := call(t, h, "wifi.apply", req)
	if err != nil || string(out) != `{"applied":true,"connected":true}` {
		t.Fatalf("apply = %s, %v", out, err)
	}
	kf := filepath.Join(h.NMConnectionsDir, wifiProfileName("lab")+".nmconnection")
	st, err := os.Stat(kf)
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("keyfile %v %v", st, err)
	}
	if data, _ := os.ReadFile(kf); !strings.Contains(string(data), "psk=secret-pass") {
		t.Error("password not in the keyfile")
	}
	for _, c := range s.calls {
		if strings.Contains(c, "secret-pass") {
			t.Fatalf("the password was on a command line: %s", c)
		}
	}
	if !s.ran("nmcli --wait 45 connection up id " + wifiProfileName("lab") + " ifname wlan0") {
		t.Errorf("not activated: %v", s.calls)
	}
	// A second change waits for the first.
	if _, err := call(t, h, "wifi.apply", req); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("second apply while pending: %v", err)
	}
	out, _ = call(t, h, "wifi.status", nil)
	if !strings.Contains(string(out), `"pending":{"id":"8a1c2d3e-0000-4000-8000-000000000001","revision":3}`) {
		t.Errorf("pending not shown: %s", out)
	}
	if _, err := call(t, h, "wifi.confirm", nil); err != nil {
		t.Fatal(err)
	}
	if h.readWifiPending() != nil {
		t.Error("still pending after confirm")
	}
	if _, err := os.Stat(kf); err != nil {
		t.Error("confirm removed the new network")
	}
}

func TestWifiApplyRollsBack(t *testing.T) {
	s, h := nmHost(t)
	// An older profile for the same network in NetworkManager's directory.
	_ = os.MkdirAll(h.NMConnectionsDir, 0o755)
	old := filepath.Join(h.NMConnectionsDir, "lab-old.nmconnection")
	_ = os.WriteFile(old, []byte("[connection]\nid=lab-old\n"), 0o600)
	s.answers["nmcli -t -f NAME,UUID,TYPE,FILENAME connection show"] = "lab-old:u9:802-11-wireless:" + old + "\n"
	s.answers["nmcli -e no -g 802-11-wireless.ssid connection show uuid u9"] = "lab\n"
	s.answers["nmcli -t -f UUID,TYPE connection show --active"] = "u9:802-11-wireless\n"

	// Cannot join: the old profile is back, the new one gone.
	s.fail["nmcli --wait"] = true
	req := WifiApplyRequest{ID: "8a1c2d3e-0000-4000-8000-000000000001", SSID: "lab", Password: "new-password", ConnectNow: true}
	if _, err := call(t, h, "wifi.apply", req); localrpc.CodeOf(err) != CodeNotJoined {
		t.Fatalf("apply: %v", err)
	}
	if _, err := os.Stat(old); err != nil {
		t.Error("the old profile was not put back")
	}
	if _, err := os.Stat(filepath.Join(h.NMConnectionsDir, wifiProfileName("lab")+".nmconnection")); err == nil {
		t.Error("the new profile stayed")
	}
	if !s.ran("nmcli connection up uuid u9") || h.readWifiPending() != nil {
		t.Errorf("not reconnected or still pending: %v", s.calls)
	}

	// Joined, but the agent cannot reach the server: rollback.
	delete(s.fail, "nmcli --wait")
	if _, err := call(t, h, "wifi.apply", req); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(old); err == nil {
		t.Fatal("the old profile was not replaced")
	}
	out, err := call(t, h, "wifi.rollback", nil)
	if err != nil || !strings.Contains(string(out), `"rolled_back":true`) {
		t.Fatalf("rollback = %s, %v", out, err)
	}
	if data, _ := os.ReadFile(old); !strings.Contains(string(data), "lab-old") {
		t.Error("rollback did not restore the old profile")
	}
	if out, _ := call(t, h, "wifi.rollback", nil); string(out) != `{"rolled_back":false,"id":"","revision":null}` {
		t.Errorf("second rollback = %s", out)
	}
}

func TestWifiApplyValidation(t *testing.T) {
	_, h := nmHost(t)
	for name, req := range map[string]WifiApplyRequest{
		"short password":  {SSID: "lab", Password: "short"},
		"long ssid":       {SSID: strings.Repeat("s", 33), Password: "long-enough"},
		"line break":      {SSID: "lab\nx", Password: "long-enough"},
		"security":        {SSID: "lab", Security: "wep", Password: "long-enough"},
		"connect_now id":  {SSID: "lab", Password: "long-enough", ConnectNow: true, ID: "nope"},
		"password breaks": {SSID: "lab", Password: "long-enough\n"},
	} {
		if _, err := call(t, h, "wifi.apply", req); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestWifiRemove(t *testing.T) {
	s, h := nmHost(t)
	_ = os.MkdirAll(h.NMConnectionsDir, 0o755)
	file := filepath.Join(h.NMConnectionsDir, "home.nmconnection")
	s.answers["nmcli -t -f NAME,UUID,TYPE,FILENAME connection show"] = "home:u1:802-11-wireless:" + file + "\n"
	// Connected through it with a cable: allowed.
	if out, err := call(t, h, "wifi.remove", WifiRemoveRequest{SSID: "home"}); err != nil || !strings.Contains(string(out), `"found":true`) {
		t.Fatalf("remove = %s, %v", out, err)
	}
	if !s.ran("nmcli connection delete uuid u1") {
		t.Error("not deleted")
	}
	// Without a cable it would cut the device off.
	s.answers["nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device"] = "eth0:ethernet:unavailable:\nwlan0:wifi:connected:home\n"
	if _, err := call(t, h, "wifi.remove", WifiRemoveRequest{SSID: "home"}); localrpc.CodeOf(err) != CodeWouldCutOff {
		t.Errorf("remove the only link: %v", err)
	}
	if out, _ := call(t, h, "wifi.remove", WifiRemoveRequest{SSID: "elsewhere"}); !strings.Contains(string(out), `"found":false`) {
		t.Errorf("unknown network = %s", out)
	}
}

func TestNetplanDefinition(t *testing.T) {
	for _, c := range []struct {
		p    nmProfile
		want string
	}{
		{nmProfile{name: "netplan-wlan0-home", file: "/run/NetworkManager/system-connections/netplan-wlan0-home.nmconnection", ssid: "home"}, "wlan0"},
		{nmProfile{name: "x", file: "/run/x/netplan-NM-1234.nmconnection", ssid: "home"}, "NM-1234"},
		{nmProfile{name: "home", file: "/etc/NetworkManager/system-connections/home.nmconnection", ssid: "home"}, ""},
		{nmProfile{name: "netplan-a.b-home", file: "/run/x/netplan-a.b-home.nmconnection", ssid: "home"}, ""},
	} {
		if got := netplanDefinition(c.p); got != c.want {
			t.Errorf("%+v = %q", c.p, got)
		}
	}
}

const (
	wgPriv = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
	wgPub  = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
)

func wgCfg() WireGuardConfig {
	return WireGuardConfig{Address: "10.8.0.12/32", PrivateKey: wgPriv, PublicKey: wgPub, Endpoint: "vpn.example.com:51820",
		AllowedIPs: "10.8.0.1", Keepalive: 25}
}

func TestWireGuardRender(t *testing.T) {
	conf, hosts, err := wgCfg().render()
	if err != nil {
		t.Fatal(err)
	}
	want := "[Interface]\nAddress = 10.8.0.12/32\nPrivateKey = " + wgPriv + "\n\n[Peer]\nPublicKey = " + wgPub +
		"\nEndpoint = vpn.example.com:51820\nAllowedIPs = 10.8.0.1/32\nPersistentKeepalive = 25\n"
	if conf != want || len(hosts) != 2 {
		t.Fatalf("conf:\n%s\nhosts %v", conf, hosts)
	}
	for name, edit := range map[string]func(*WireGuardConfig){
		"network":        func(c *WireGuardConfig) { c.AllowedIPs = "10.8.0.0/24" },
		"default route":  func(c *WireGuardConfig) { c.AllowedIPs = "0.0.0.0/0" },
		"public address": func(c *WireGuardConfig) { c.Address = "8.8.8.8/32" },
		"bad key":        func(c *WireGuardConfig) { c.PublicKey = "short" },
		"hook":           func(c *WireGuardConfig) { c.Endpoint = "x;PostUp=evil:1" },
		"option":         func(c *WireGuardConfig) { c.Endpoint = "-x:1" },
		"port":           func(c *WireGuardConfig) { c.Endpoint = "vpn.example.com:70000" },
		"newline":        func(c *WireGuardConfig) { c.Address = "10.8.0.12/32\nPostUp = evil" },
		"keepalive":      func(c *WireGuardConfig) { c.Keepalive = -1 },
	} {
		c := wgCfg()
		edit(&c)
		if _, _, err := c.render(); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestWireGuardApply(t *testing.T) {
	s := &scripted{answers: map[string]string{"ip -j route get": `[{"dst":"10.8.0.12","gateway":"192.168.0.1","dev":"eth0"}]`}}
	h := s.host(t)
	// wg-quick creates and removes the interface, as the kernel would.
	run := h.Run
	h.Run = func(ctx context.Context, name string, args ...string) ([]byte, error) {
		out, err := run(ctx, name, args...)
		if err == nil && name == "wg-quick" && len(args) > 0 {
			if args[0] == "up" {
				_ = os.MkdirAll(filepath.Join(h.SysNetDir, "wg0"), 0o755)
			} else {
				_ = os.RemoveAll(filepath.Join(h.SysNetDir, "wg0"))
			}
		}
		return out, err
	}
	out, err := call(t, h, "wireguard.apply", wgCfg())
	if err != nil || string(out) != `{"changed":true}` {
		t.Fatalf("apply = %s, %v", out, err)
	}
	if st, err := os.Stat(h.wgConf()); err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("config %v %v", st, err)
	}
	if !s.ran("wg-quick up " + h.wgConf()) {
		t.Fatalf("not brought up: %v", s.calls)
	}
	// wg0 runs that config: nothing to do.
	runs := len(s.calls)
	if out, _ := call(t, h, "wireguard.apply", wgCfg()); string(out) != `{"changed":false}` || len(s.calls) != runs {
		t.Errorf("unchanged apply = %s, ran %v", out, s.calls[runs:])
	}
	// A new endpoint restarts it.
	c := wgCfg()
	c.Endpoint = "192.0.2.10:51820"
	if out, _ := call(t, h, "wireguard.apply", c); string(out) != `{"changed":true}` || !s.ran("wg-quick down "+h.wgConf()) {
		t.Errorf("changed apply = %s, ran %v", out, s.calls[runs:])
	}
	// A tunnel host on the LAN is refused before the working tunnel is touched.
	s.answers["ip -j route get"] = `[{"dst":"10.8.0.12","dev":"eth0","prefsrc":"10.8.0.2"}]`
	c.Endpoint = "192.0.2.11:51820"
	runs = len(s.calls)
	if _, err := call(t, h, "wireguard.apply", c); err == nil || !strings.Contains(err.Error(), "local network") {
		t.Errorf("LAN host: %v", err)
	}
	if s.ran("wg-quick down") && slices.ContainsFunc(s.calls[runs:], func(c string) bool { return strings.HasPrefix(c, "wg-quick down") }) {
		t.Errorf("the working tunnel was taken down: %v", s.calls[runs:])
	}
	// A route that cannot be checked is refused too.
	s.answers["ip -j route get"] = "not json"
	if _, err := call(t, h, "wireguard.apply", c); err == nil || !strings.Contains(err.Error(), "cannot check") {
		t.Errorf("unchecked route: %v", err)
	}
	// wg-quick fails to start the new config: the previous tunnel comes back.
	s.answers["ip -j route get"] = `[{"dst":"10.8.0.12","gateway":"192.168.0.1","dev":"eth0"}]`
	working, _ := os.ReadFile(h.wgConf())
	s.fail = map[string]bool{"wg-quick up": true}
	if _, err := call(t, h, "wireguard.apply", wgCfg()); err == nil {
		t.Fatal("a failed start was reported as applied")
	}
	if now, _ := os.ReadFile(h.wgConf()); string(now) != string(working) {
		t.Errorf("the previous config was not put back")
	}
	s.fail = nil
	// A teardown that leaves wg0 up is an error.
	_ = os.MkdirAll(filepath.Join(h.SysNetDir, "wg0"), 0o755)
	s.fail = map[string]bool{"wg-quick down": true, "ip link delete": true}
	if _, err := call(t, h, "wireguard.down", nil); err == nil {
		t.Error("a failed teardown was reported as down")
	}
	s.fail = nil
}

func TestWireGuardShowAndEndpoint(t *testing.T) {
	s := &scripted{answers: map[string]string{
		"wg show wg0 latest-handshakes": wgPub + "\t1700000000\n",
		"wg show wg0 endpoints":         wgPub + "\t192.0.2.1:51820\n",
	}}
	h := s.host(t)
	if out, _ := call(t, h, "wireguard.show", nil); string(out) != `{"up":false,"peers":[]}` {
		t.Errorf("down = %s", out)
	}
	_ = os.MkdirAll(filepath.Join(h.SysNetDir, "wg0"), 0o755)
	out, _ := call(t, h, "wireguard.show", nil)
	if !strings.Contains(string(out), `"latest_handshake":1700000000`) || !strings.Contains(string(out), `"endpoint":"192.0.2.1:51820"`) {
		t.Errorf("show = %s", out)
	}
	if _, err := call(t, h, "wireguard.endpoint", WireGuardEndpointRequest{PublicKey: wgPub, Endpoint: "vpn.example.com:51820"}); err == nil {
		t.Error("a name was accepted as the resolved endpoint")
	}
	if _, err := call(t, h, "wireguard.endpoint", WireGuardEndpointRequest{PublicKey: wgPub, Endpoint: "192.0.2.9:51820"}); err != nil ||
		!s.ran("wg set wg0 peer "+wgPub+" endpoint 192.0.2.9:51820") {
		t.Errorf("endpoint: %v %v", err, s.calls)
	}
}

func TestSyncKeyLines(t *testing.T) {
	gw := "ssh-ed25519 AAAAgateway"
	ca := "ssh-ed25519 AAAAca"
	mine := "ssh-rsa AAAAmine me@laptop"
	lines, trusted := syncKeyLines([]string{mine}, SSHKeysRequest{GatewayKey: gw + " ignored", CAKey: ca, Principal: "device-1"})
	want := []string{mine, gw + " meteorcloud-gateway", `cert-authority,principals="device-1" ` + ca + " meteorcloud-ca"}
	if !slices.Equal(lines, want) || trusted != ca {
		t.Fatalf("lines %q trusted %q", lines, trusted)
	}
	// Nothing new: unchanged. An empty answer never removes lines.
	if again, _ := syncKeyLines(lines, SSHKeysRequest{}); !slices.Equal(again, lines) {
		t.Errorf("empty request changed %q", again)
	}
	// A rotated gateway key replaces the old line; retired removes it.
	rotated, _ := syncKeyLines(lines, SSHKeysRequest{GatewayKey: "ssh-ed25519 AAAAnew"})
	if slices.Contains(rotated, want[1]) || !slices.Contains(rotated, "ssh-ed25519 AAAAnew meteorcloud-gateway") {
		t.Errorf("rotated %q", rotated)
	}
	retired, _ := syncKeyLines(append(rotated, "ssh-ed25519 AAAAold meteorcloud-shell-gateway"), SSHKeysRequest{GatewayKeyRetired: true})
	if !slices.Equal(retired, []string{mine, want[2]}) {
		t.Errorf("retired %q", retired)
	}
}

func TestSSHAuthorizedKeys(t *testing.T) {
	s := &scripted{}
	h := s.host(t)
	home := t.TempDir()
	uid, gid := os.Getuid(), os.Getgid()
	h.LookupUser = func(name string) (UserInfo, error) {
		if name != "pi" {
			return UserInfo{}, errNoUser
		}
		return UserInfo{UID: uid, GID: gid, Home: home}, nil
	}
	req := SSHKeysRequest{CAKey: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOps ca", Principal: "device-1"}
	if _, err := call(t, h, "ssh.authorized_keys", req); localrpc.CodeOf(err) != CodeUnsupported {
		t.Fatalf("without ssh_user: %v", err)
	}
	_ = os.WriteFile(h.ConfigFile, []byte(`{"version":1,"ssh_user":"pi"}`), 0o640)
	out, err := call(t, h, "ssh.authorized_keys", req)
	if err != nil || !strings.Contains(string(out), `"trusted_ca":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOps"`) ||
		!strings.Contains(string(out), `"changed":true`) {
		t.Fatalf("sync = %s, %v", out, err)
	}
	path := filepath.Join(home, ".ssh", "authorized_keys")
	st, err := os.Stat(path)
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("authorized_keys %v %v", st, err)
	}
	if out, _ := call(t, h, "ssh.authorized_keys", SSHKeysRequest{}); !strings.Contains(string(out), `"changed":false`) ||
		!strings.Contains(string(out), "AAAAC3NzaC1lZDI1NTE5AAAAIOps") {
		t.Errorf("report only = %s", out)
	}
	for name, bad := range map[string]SSHKeysRequest{
		"principal": {CAKey: req.CAKey, Principal: `x" ,command="evil`},
		"key":       {GatewayKey: "not a key"},
	} {
		if _, err := call(t, h, "ssh.authorized_keys", bad); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: %v", name, err)
		}
	}
	// root is never the SSH user.
	_ = os.WriteFile(h.ConfigFile, []byte(`{"version":1,"ssh_user":"root"}`), 0o640)
	if _, err := call(t, h, "ssh.authorized_keys", req); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("root: %v", err)
	}
}

func TestLogSources(t *testing.T) {
	s := &scripted{answers: map[string]string{
		"systemctl list-units": "nginx.service loaded active running Web\nbad name.service loaded active running X\n" +
			"gone.service not-found inactive dead Gone\n",
		"docker ps --all": "web\trunning\tshop\ncron\texited\t\n-bad\trunning\t\n",
	}}
	h := s.host(t)
	_ = os.WriteFile(h.DockerSocket, nil, 0o600)
	logDir := t.TempDir()
	_ = os.WriteFile(filepath.Join(logDir, "syslog"), []byte("x\n"), 0o644)
	_ = os.Symlink(filepath.Join(logDir, "syslog"), filepath.Join(logDir, "link.log"))
	h.LogGlobs = []string{filepath.Join(logDir, "syslog"), filepath.Join(logDir, "*.log")}

	got := ListLogs(context.Background(), h)
	var specs []string
	for _, src := range got {
		specs = append(specs, src.Kind+":"+src.Name+":"+src.State+":"+src.Project)
	}
	want := []string{"journal:::", "systemd:nginx.service:active:", "docker:web:running:shop", "docker:cron:exited:",
		"file:" + filepath.Join(logDir, "syslog") + "::"}
	if !slices.Equal(specs, want) {
		t.Fatalf("sources %q", specs)
	}
	data, _ := json.Marshal(got[0])
	if string(data) != `{"kind":"journal","name":""}` {
		t.Errorf("journal JSON %s (the control plane parses this shape)", data)
	}
}

func TestShowLogs(t *testing.T) {
	s := &scripted{answers: map[string]string{
		"systemctl list-units": "nginx.service loaded active running Web\n",
		"docker ps --all":      "web\trunning\tshop\n",
		"journalctl":           "Error one\ninfo\nERROR two\n",
		"docker logs":          "line\n",
	}}
	h := s.host(t)
	_ = os.WriteFile(h.DockerSocket, nil, 0o600)
	var out strings.Builder
	ctx := context.Background()
	if err := ShowLogs(ctx, h, ShowLogsRequest{Kind: "systemd", Name: "nginx.service", Lines: 50, Since: "1h"}, &out); err != nil {
		t.Fatal(err)
	}
	if !s.ran("journalctl --no-pager --quiet --output=short-iso --lines=50 --unit=nginx.service --since=-1h") {
		t.Errorf("journalctl %v", s.calls)
	}
	out.Reset()
	if err := ShowLogs(ctx, h, ShowLogsRequest{Kind: "journal", Grep: "error", Lines: 1}, &out); err != nil || out.String() != "ERROR two\n" {
		t.Errorf("grep = %q, %v", out.String(), err)
	}
	if err := ShowLogs(ctx, h, ShowLogsRequest{Kind: "docker", Name: "web", Project: "shop", Since: "15m", Follow: true}, io.Discard); err != nil ||
		!s.ran("docker logs --timestamps --tail 200 --since 900s --follow -- web") {
		t.Errorf("docker: %v %v", err, s.calls)
	}
	for name, r := range map[string]ShowLogsRequest{
		"not listed":    {Kind: "systemd", Name: "ssh.service"},
		"other project": {Kind: "docker", Name: "web", Project: "other"},
		"file":          {Kind: "file", Name: "/etc/shadow"},
		"option name":   {Kind: "docker", Name: "-v"},
		"lines":         {Kind: "journal", Lines: 10001},
		"since":         {Kind: "journal", Since: "yesterday"},
		"kind":          {Kind: "kernel"},
	} {
		if err := ShowLogs(ctx, h, r, io.Discard); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestGrepWriter(t *testing.T) {
	var out strings.Builder
	g := &grepWriter{needle: []byte("warn"), w: &out}
	_, _ = g.Write([]byte("a WARN\nok\nwar"))
	_, _ = g.Write([]byte("ning here\n"))
	if out.String() != "a WARN\nwarning here\n" {
		t.Errorf("%q", out.String())
	}
	// A match past the displayed prefix of a long line, split between writes.
	out.Reset()
	_, _ = g.Write([]byte(strings.Repeat("x", maxLineBytes+100) + "wa"))
	_, _ = g.Write([]byte("rn tail\nnope\n"))
	if !strings.HasPrefix(out.String(), "xxx") || strings.Count(out.String(), "\n") != 1 || len(out.String()) != maxLineBytes+1 {
		t.Errorf("long line: %d bytes, %d lines", len(out.String()), strings.Count(out.String(), "\n"))
	}
}

func TestForgetInNetplanUsesPersistentAccessPoints(t *testing.T) {
	s := &scripted{answers: map[string]string{
		"netplan get wifis.wlan0.access-points": "lab:\n  password: x\nbackup:\n  password: y\n",
	}}
	h := s.host(t)
	h.LookNetplan = func() bool { return true }
	lab := nmProfile{name: "netplan-wlan0-lab", uuid: "u1", file: "/run/NetworkManager/system-connections/netplan-wlan0-lab.nmconnection", ssid: "lab"}
	// nmcli could not read backup's SSID, so only lab shows at runtime: the
	// definition still has two networks and only lab goes.
	if !h.forgetInNetplan(context.Background(), []nmProfile{lab}, "lab") {
		t.Fatalf("not done: %v", s.calls)
	}
	if !s.ran("netplan set wifis.wlan0.access-points.lab=null") || s.ran("netplan set wifis.wlan0=null") {
		t.Errorf("runs %v", s.calls)
	}
	// Only network in the definition: the definition goes.
	s.answers["netplan get wifis.wlan0.access-points"] = "\"lab\":\n  password: x\n"
	s.calls = nil
	if !h.forgetInNetplan(context.Background(), []nmProfile{lab}, "lab") || !s.ran("netplan set wifis.wlan0=null") {
		t.Errorf("runs %v", s.calls)
	}
	// The definition cannot be read: nothing is changed.
	s.fail = map[string]bool{"netplan get": true}
	s.calls = nil
	if h.forgetInNetplan(context.Background(), []nmProfile{lab}, "lab") || s.ran("netplan set") {
		t.Errorf("changed an unread definition: %v", s.calls)
	}
}

func TestWifiRollbackKeepsPendingWhenRestoreFails(t *testing.T) {
	s, h := nmHost(t)
	_ = os.MkdirAll(h.NMConnectionsDir, 0o755)
	old := filepath.Join(h.NMConnectionsDir, "lab-old.nmconnection")
	_ = os.WriteFile(old, []byte("[connection]\nid=lab-old\n"), 0o600)
	s.answers["nmcli -t -f NAME,UUID,TYPE,FILENAME connection show"] = "lab-old:u9:802-11-wireless:" + old + "\n"
	s.answers["nmcli -e no -g 802-11-wireless.ssid connection show uuid u9"] = "lab\n"
	req := WifiApplyRequest{ID: "8a1c2d3e-0000-4000-8000-000000000001", SSID: "lab", Password: "new-password", ConnectNow: true}
	if _, err := call(t, h, "wifi.apply", req); err != nil {
		t.Fatal(err)
	}
	// The backup is gone (disk trouble): the rollback fails and stays pending.
	_ = os.RemoveAll(filepath.Join(h.wifiDir(), wifiBackupDir))
	if _, err := call(t, h, "wifi.rollback", nil); err == nil {
		t.Fatal("a failed restore reported rolled back")
	}
	if h.readWifiPending() == nil {
		t.Error("the pending record was dropped after a failed restore")
	}
	// Save-for-later: a failed reload is not reported applied.
	_, _ = call(t, h, "wifi.confirm", nil)
	s.fail["nmcli connection reload"] = true
	if _, err := call(t, h, "wifi.apply", WifiApplyRequest{SSID: "other", Password: "long-enough"}); err == nil {
		t.Error("a profile NetworkManager did not load was reported applied")
	}
}

func TestWifiRadioOff(t *testing.T) {
	radioWait = 20 * time.Millisecond
	req := WifiApplyRequest{ID: "8a1c2d3e-0000-4000-8000-000000000009", SSID: "lab", Password: "long-password", ConnectNow: true}

	// The radio is off and the adapter stays unavailable (hardware block):
	// refused before anything is written.
	s, h := nmHost(t)
	s.answers["nmcli -t -f WIFI radio"] = "disabled\n"
	s.answers["nmcli -t -f WIFI-HW radio"] = "disabled\n"
	s.answers["nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device"] = "eth0:ethernet:connected:Wired\nwlan0:wifi:unavailable:\n"
	_, err := call(t, h, "wifi.apply", req)
	if localrpc.CodeOf(err) != CodeRadioOff || !strings.Contains(err.Error(), "hardware switch") {
		t.Fatalf("err = %v", err)
	}
	if !s.ran("nmcli radio wifi on") || h.readWifiPending() != nil || s.ran("nmcli --wait") {
		t.Errorf("runs %v", s.calls)
	}
	if entries, _ := os.ReadDir(h.NMConnectionsDir); len(entries) != 0 {
		t.Errorf("profiles written: %v", entries)
	}

	// The radio comes on: the adapter is scanned and the change goes ahead.
	s, h = nmHost(t)
	s.answers["nmcli -t -f WIFI radio"] = "disabled\n"
	s.answers["nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device"] = "eth0:ethernet:connected:Wired\nwlan0:wifi:disconnected:\n"
	if _, err := call(t, h, "wifi.apply", req); err != nil {
		t.Fatal(err)
	}
	if !s.ran("nmcli radio wifi on") || !s.ran("nmcli -t -f SSID device wifi list --rescan yes ifname wlan0") {
		t.Errorf("runs %v", s.calls)
	}

	// The radio is already on: left alone.
	s, h = nmHost(t)
	s.answers["nmcli -t -f WIFI radio"] = "enabled\n"
	if _, err := call(t, h, "wifi.apply", WifiApplyRequest{ID: req.ID, SSID: "lab2", Password: "long-password", ConnectNow: true}); err != nil {
		t.Fatal(err)
	}
	if s.ran("nmcli radio wifi on") {
		t.Error("an enabled radio was switched")
	}
}

func TestJoinFailure(t *testing.T) {
	// What nmcli 1.52 prints for a wrong WPA password (stdout; stderr starts with a warning).
	if got := joinFailure("Passwords or encryption keys are required to access the wireless network 'lab'.\n",
		errors.New("nmcli --wait 90 connection up: exit status 4: Warning: password for '802-11-wireless-security.psk' not given")); !strings.Contains(got, "password was not accepted") {
		t.Errorf("wrong password: %q", got)
	}
	for _, c := range []struct{ nmcli, want string }{
		{"Error: Connection activation failed: Secrets were required, but not provided", "password was not accepted"},
		{"Error: Connection activation failed: No network with SSID 'lab' found.", "not found"},
		{"Error: Connection activation failed: (53) The Wi-Fi network could not be found", "not found"},
		{"Error: Connection 'x' is not available on device wlan0 because device is not available", "adapter is not ready"},
		{"Error: something else", "wrong password, or out of range"},
	} {
		if got := joinFailure("", errors.New(c.nmcli)); !strings.Contains(got, c.want) {
			t.Errorf("%q: %q", c.nmcli, got)
		}
	}
}
