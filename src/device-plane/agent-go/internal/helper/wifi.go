package helper

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"

	"meteorcloud/meteorcli/internal/localrpc"
	"meteorcloud/meteorcli/internal/platform"
)

// Wi-Fi through NetworkManager: the Python meteorcli-wifi helper. It writes a
// keyfile for one network (name, password, hidden) and runs fixed nmcli
// commands. The password arrives in the request, is written only to a 0600
// keyfile, and is never on a command line or in a log line.
//
// wifi.apply with connect_now switches at once and keeps the old profiles
// aside until wifi.confirm. If the network cannot be joined it puts them back
// itself (code not_joined); if the agent then cannot reach MeteorCloud it calls
// wifi.rollback. A change interrupted by a power cut shows as "pending" in
// wifi.status and the agent rolls it back when it starts. A network the OS's
// netplan configuration defines is removed from the netplan YAML too.

// Error codes the agent branches on.
const (
	CodeNotJoined   = "not_joined"    // the network could not be joined; the old settings are back
	CodeWouldCutOff = "would_cut_off" // removing the network would leave the device offline
	CodeUnsupported = "unsupported"   // Wi-Fi cannot be managed on this device
)

const (
	wifiProfilePrefix  = "meteor-wifi"
	wifiPriority       = 10 // above profiles written at image time, so a changed password wins
	wifiActivateSecs   = 45
	netplanPrefix      = "netplan-"
	wifiPendingFile    = "pending.json"
	wifiBackupDir      = "backup"
	wifiSecurityPSK    = "wpa-psk"
	wifiSecurityOpen   = "open"
	nmWirelessType     = "802-11-wireless"
	nmKeyfileExtension = ".nmconnection"
)

// WifiApplyRequest is the params of wifi.apply. Password is a secret.
type WifiApplyRequest struct {
	ID         string `json:"id"`
	Revision   *int   `json:"revision"`
	SSID       string `json:"ssid"`
	Security   string `json:"security"`
	Password   string `json:"password"`
	Hidden     bool   `json:"hidden"`
	ConnectNow bool   `json:"connect_now"`
}

// WifiRemoveRequest is the params of wifi.remove.
type WifiRemoveRequest struct {
	SSID string `json:"ssid"`
}

// WifiNetwork is one saved network; Managed says MeteorCloud wrote it.
type WifiNetwork struct {
	SSID    string `json:"ssid"`
	Managed bool   `json:"managed"`
}

// WifiPending is a connect_now change waiting for confirm or rollback.
type WifiPending struct {
	ID       string `json:"id"`
	Revision *int   `json:"revision"`
}

// WifiStatus is the answer of wifi.status, in the report's shape (no passwords).
type WifiStatus struct {
	Supported         bool          `json:"supported"`
	UnsupportedReason *string       `json:"unsupported_reason"`
	ConnectedSSID     *string       `json:"connected_ssid,omitempty"`
	SignalPercent     *int          `json:"signal_percent,omitempty"`
	Interface         *string       `json:"interface,omitempty"`
	WiredConnected    bool          `json:"wired_connected"`
	SavedNetworks     []WifiNetwork `json:"saved_networks"`
	Pending           *WifiPending  `json:"pending,omitempty"`
	platform.Uplink
}

// WifiApplyReply is the answer of wifi.apply.
type WifiApplyReply struct {
	Applied   bool `json:"applied"`
	Connected bool `json:"connected"`
}

// WifiRollbackReply is the answer of wifi.rollback.
type WifiRollbackReply struct {
	RolledBack bool `json:"rolled_back"`
	WifiPending
}

// wifiPendingState is the helper's own record of a connect_now change.
type wifiPendingState struct {
	ID           string   `json:"id"`
	Revision     *int     `json:"revision"`
	NewFile      string   `json:"new_file"`
	Backups      []string `json:"backups"`
	PreviousUUID string   `json:"previous_uuid"`
}

type nmProfile struct{ name, uuid, file, ssid string }

func (h Host) wifiDir() string { return filepath.Join(h.StateDir, "wifi") }

// nmcli runs nmcli and returns its stdout only (warnings go to stderr).
func (h Host) nmcli(ctx context.Context, args ...string) (string, error) {
	var out bytes.Buffer
	err := h.Stream(ctx, &capWriter{w: &out, max: maxOutput}, "nmcli", args...)
	return out.String(), err
}

// terse runs `nmcli -t` and splits its lines into fields.
func (h Host) terse(ctx context.Context, args ...string) [][]string {
	out, err := h.nmcli(ctx, append([]string{"-t"}, args...)...)
	if err != nil {
		return nil
	}
	var rows [][]string
	for _, line := range strings.Split(out, "\n") {
		if line != "" {
			rows = append(rows, splitTerse(line))
		}
	}
	return rows
}

// splitTerse splits one `nmcli -t` line: ':' separates, '\:' and '\\' are literal.
func splitTerse(line string) []string {
	var fields []string
	var cur strings.Builder
	escaped := false
	for _, r := range line {
		switch {
		case escaped:
			cur.WriteRune(r)
			escaped = false
		case r == '\\':
			escaped = true
		case r == ':':
			fields = append(fields, cur.String())
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	return append(fields, cur.String())
}

func (h Host) unsupportedReason(ctx context.Context) string {
	out, err := h.nmcli(ctx, "-t", "-f", "RUNNING", "general")
	switch {
	case err != nil && strings.Contains(err.Error(), "not found"):
		return "NetworkManager is not installed on this device."
	case err != nil || strings.TrimSpace(out) != "running":
		return "NetworkManager is not running on this device."
	}
	if h.wifiInterface(ctx) == "" {
		return "This device has no Wi-Fi adapter."
	}
	return ""
}

type nmDevice struct{ name, kind, state string }

func (h Host) devices(ctx context.Context) []nmDevice {
	var out []nmDevice
	for _, row := range h.terse(ctx, "-f", "DEVICE,TYPE,STATE,CONNECTION", "device") {
		if len(row) >= 3 {
			out = append(out, nmDevice{row[0], row[1], row[2]})
		}
	}
	return out
}

func (h Host) wifiInterface(ctx context.Context) string {
	for _, d := range h.devices(ctx) {
		if d.kind == "wifi" {
			return d.name
		}
	}
	return ""
}

func (h Host) wiredConnected(ctx context.Context) bool {
	for _, d := range h.devices(ctx) {
		if d.kind == "ethernet" && strings.HasPrefix(d.state, "connected") {
			return true
		}
	}
	return false
}

// activeWifi is the SSID and signal of the network in use.
func (h Host) activeWifi(ctx context.Context) (string, *int) {
	for _, row := range h.terse(ctx, "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no") {
		if len(row) >= 3 && row[0] == "yes" {
			if n, err := strconv.Atoi(row[2]); err == nil {
				return row[1], &n
			}
			return row[1], nil
		}
	}
	return "", nil
}

func (h Host) wifiProfiles(ctx context.Context) []nmProfile {
	var out []nmProfile
	for _, row := range h.terse(ctx, "-f", "NAME,UUID,TYPE,FILENAME", "connection", "show") {
		if len(row) < 4 || row[2] != nmWirelessType {
			continue
		}
		// -e no: one value, so ':' and '\' in a network name come back as they are.
		ssid, err := h.nmcli(ctx, "-e", "no", "-g", nmWirelessType+".ssid", "connection", "show", "uuid", row[1])
		if err != nil {
			continue
		}
		out = append(out, nmProfile{name: row[0], uuid: row[1], file: row[3], ssid: strings.TrimRight(ssid, "\n")})
	}
	return out
}

func (h Host) activeWifiUUID(ctx context.Context) string {
	for _, row := range h.terse(ctx, "-f", "UUID,TYPE", "connection", "show", "--active") {
		if len(row) >= 2 && row[1] == nmWirelessType {
			return row[0]
		}
	}
	return ""
}

// inConnectionsDir says a profile is a file in NetworkManager's keyfile
// directory (not one generated at boot in /run, such as netplan's).
func (h Host) inConnectionsDir(path string) bool {
	real, err := filepath.EvalSymlinks(filepath.Dir(path))
	dir, err2 := filepath.EvalSymlinks(h.NMConnectionsDir)
	return err == nil && err2 == nil && real == dir
}

func validSSID(s string) error {
	if len(s) < 1 || len(s) > 32 || !utf8.ValidString(s) {
		return localrpc.Errorf(localrpc.CodeBadRequest, "ssid must be 1 to 32 bytes")
	}
	if strings.ContainsAny(s, "\x00\r\n") {
		return localrpc.Errorf(localrpc.CodeBadRequest, "ssid must not contain line breaks")
	}
	return nil
}

func validPSK(s string) error {
	if n := utf8.RuneCountInString(s); n < 8 || n > 63 {
		return localrpc.Errorf(localrpc.CodeBadRequest, "password must be 8 to 63 characters")
	}
	if strings.ContainsAny(s, "\x00\r\n") {
		return localrpc.Errorf(localrpc.CodeBadRequest, "password must not contain line breaks")
	}
	return nil
}

// keyfileEscape quotes a keyfile value: ';' separates lists, '\' escapes, and
// spaces at the edges are kept.
func keyfileEscape(v string) string {
	out := strings.ReplaceAll(strings.ReplaceAll(v, `\`, `\\`), ";", `\;`)
	if strings.HasPrefix(out, " ") {
		out = `\s` + out[1:]
	}
	if strings.HasSuffix(out, " ") {
		out = out[:len(out)-1] + `\s`
	}
	return out
}

func wifiProfileName(ssid string) string {
	sum := sha256.Sum256([]byte(ssid))
	return wifiProfilePrefix + "-" + hex.EncodeToString(sum[:])[:10]
}

func newUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	x := hex.EncodeToString(b[:])
	return x[:8] + "-" + x[8:12] + "-" + x[12:16] + "-" + x[16:20] + "-" + x[20:]
}

func wifiKeyfile(r WifiApplyRequest) string {
	lines := []string{
		"[connection]", "id=" + wifiProfileName(r.SSID), "uuid=" + newUUID(), "type=wifi", "autoconnect=true",
		"autoconnect-priority=" + strconv.Itoa(wifiPriority), "",
		"[wifi]", "mode=infrastructure", "ssid=" + keyfileEscape(r.SSID),
	}
	if r.Hidden {
		lines = append(lines, "hidden=true")
	}
	if r.Security == wifiSecurityPSK {
		lines = append(lines, "", "[wifi-security]", "key-mgmt=wpa-psk", "psk="+keyfileEscape(r.Password))
	}
	lines = append(lines, "", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", "")
	return strings.Join(lines, "\n")
}

func (h Host) readWifiPending() *wifiPendingState {
	data, err := os.ReadFile(filepath.Join(h.wifiDir(), wifiPendingFile))
	if err != nil {
		return nil
	}
	var p wifiPendingState
	if json.Unmarshal(data, &p) != nil {
		return nil
	}
	return &p
}

func (h Host) clearWifiPending() {
	_ = os.RemoveAll(filepath.Join(h.wifiDir(), wifiBackupDir))
	_ = os.Remove(filepath.Join(h.wifiDir(), wifiPendingFile))
}

// wifiMu makes each Wi-Fi change one critical section: the pending check, the
// backups and the profile writes of one change never interleave with another's.
var wifiMu sync.Mutex

// restoreWifi puts the old profiles back, removes the new one, and reconnects.
// An error means the old settings are not all back: the caller keeps the
// pending record and backups so a later rollback can finish the job.
// Reconnecting is best effort (the old profiles are in place either way).
func (h Host) restoreWifi(ctx context.Context, p *wifiPendingState) error {
	backedUp := map[string]bool{}
	for _, name := range p.Backups {
		backedUp[filepath.Base(name)] = true
	}
	if p.NewFile != "" && h.inConnectionsDir(p.NewFile) && !backedUp[filepath.Base(p.NewFile)] {
		if err := os.Remove(p.NewFile); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("remove the new profile: %w", err)
		}
	}
	for name := range backedUp {
		data, err := os.ReadFile(filepath.Join(h.wifiDir(), wifiBackupDir, name))
		if err != nil {
			return fmt.Errorf("read the backup of %s: %w", name, err)
		}
		if err := writePrivate(filepath.Join(h.NMConnectionsDir, name), data); err != nil {
			return fmt.Errorf("restore %s: %w", name, err)
		}
	}
	if _, err := h.nmcli(ctx, "connection", "reload"); err != nil {
		return fmt.Errorf("NetworkManager did not reload the old profiles: %w", err)
	}
	if p.PreviousUUID != "" {
		if _, err := h.nmcli(ctx, "connection", "up", "uuid", p.PreviousUUID); err == nil {
			return nil
		}
	}
	if iface := h.wifiInterface(ctx); iface != "" {
		_, _ = h.nmcli(ctx, "device", "connect", iface)
	}
	return nil
}

// writePrivate creates path readable by root only, atomically (it holds a password).
func writePrivate(path string, data []byte) error {
	tmp := fmt.Sprintf("%s.tmp-%d", path, os.Getpid())
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscallNoFollow, 0o600)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(tmp, path)
	}
	if err != nil {
		_ = os.Remove(tmp)
	}
	return err
}

func (h Host) wifiStatus(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	st := WifiStatus{SavedNetworks: []WifiNetwork{}, Uplink: h.uplink()}
	if reason := h.unsupportedReason(ctx); reason != "" {
		st.UnsupportedReason = &reason
		return st, nil
	}
	st.Supported = true
	if ssid, signal := h.activeWifi(ctx); ssid != "" {
		st.ConnectedSSID, st.SignalPercent = &ssid, signal
	}
	if iface := h.wifiInterface(ctx); iface != "" {
		st.Interface = &iface
	}
	st.WiredConnected = h.wiredConnected(ctx)
	saved := map[string]bool{}
	var order []string
	for _, p := range h.wifiProfiles(ctx) {
		if p.ssid == "" {
			continue
		}
		if _, ok := saved[p.ssid]; !ok {
			order = append(order, p.ssid)
		}
		saved[p.ssid] = saved[p.ssid] || strings.HasPrefix(p.name, wifiProfilePrefix)
	}
	sortStrings(order)
	for _, ssid := range order {
		st.SavedNetworks = append(st.SavedNetworks, WifiNetwork{SSID: ssid, Managed: saved[ssid]})
	}
	if p := h.readWifiPending(); p != nil {
		st.Pending = &WifiPending{ID: p.ID, Revision: p.Revision}
	}
	return st, nil
}

func (h Host) uplink() platform.Uplink {
	if h.Uplink != nil {
		return h.Uplink()
	}
	return platform.SystemUplink()
}

func (h Host) requireWifi(ctx context.Context) error {
	if reason := h.unsupportedReason(ctx); reason != "" {
		return localrpc.Errorf(CodeUnsupported, "%s", reason)
	}
	return nil
}

func (h Host) wifiApply(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WifiApplyRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := validSSID(r.SSID); err != nil {
		return nil, err
	}
	if r.Security == "" {
		r.Security = wifiSecurityPSK
	}
	switch r.Security {
	case wifiSecurityPSK:
		if err := validPSK(r.Password); err != nil {
			return nil, err
		}
	case wifiSecurityOpen:
		r.Password = ""
	default:
		return nil, localrpc.Errorf(localrpc.CodeBadRequest, "security must be wpa-psk or open")
	}
	if r.ConnectNow && !uuidRe.MatchString(r.ID) {
		return nil, localrpc.Errorf(localrpc.CodeBadRequest, "id must be a UUID")
	}
	if err := h.requireWifi(ctx); err != nil {
		return nil, err
	}
	wifiMu.Lock()
	defer wifiMu.Unlock()
	if h.readWifiPending() != nil {
		return nil, localrpc.Errorf(localrpc.CodeBadRequest, "an earlier change is still waiting for confirm or rollback")
	}

	name := wifiProfileName(r.SSID)
	newFile := filepath.Join(h.NMConnectionsDir, name+nmKeyfileExtension)
	// Older profiles for this network (one written at image time, or our own)
	// are replaced; ones generated at boot (netplan, in /run) stay, with a lower priority.
	var replaced []nmProfile
	for _, p := range h.wifiProfiles(ctx) {
		if st, err := os.Stat(p.file); p.ssid == r.SSID && h.inConnectionsDir(p.file) && err == nil && st.Mode().IsRegular() {
			replaced = append(replaced, p)
		}
	}
	previous := h.activeWifiUUID(ctx)
	if err := os.MkdirAll(h.NMConnectionsDir, 0o755); err != nil {
		return nil, err
	}
	if r.ConnectNow {
		backup := filepath.Join(h.wifiDir(), wifiBackupDir)
		_ = os.RemoveAll(backup)
		if err := os.MkdirAll(backup, 0o700); err != nil {
			return nil, err
		}
		pending := wifiPendingState{ID: r.ID, Revision: r.Revision, NewFile: newFile, PreviousUUID: previous, Backups: []string{}}
		for _, p := range replaced {
			data, err := os.ReadFile(p.file)
			if err != nil {
				return nil, err
			}
			if err := writePrivate(filepath.Join(backup, filepath.Base(p.file)), data); err != nil {
				return nil, err
			}
			pending.Backups = append(pending.Backups, filepath.Base(p.file))
		}
		data, _ := json.Marshal(pending)
		if err := writePrivate(filepath.Join(h.wifiDir(), wifiPendingFile), data); err != nil {
			return nil, err
		}
	}
	// The new profile first: if it cannot be written, the old ones stay.
	if err := writePrivate(newFile, []byte(wifiKeyfile(r))); err != nil {
		if r.ConnectNow {
			h.clearWifiPending()
		}
		return nil, err
	}
	for _, p := range replaced {
		if p.file != newFile {
			_ = os.Remove(p.file)
		}
	}
	if _, err := h.nmcli(ctx, "connection", "reload"); err != nil {
		if r.ConnectNow {
			return nil, h.rollBackOrKeep(ctx, fmt.Errorf("NetworkManager did not load the new profile: %w", err))
		}
		return nil, fmt.Errorf("NetworkManager did not load the new profile (it loads it at the next reload or boot): %w", err)
	}
	if !r.ConnectNow {
		return WifiApplyReply{Applied: true}, nil
	}
	args := []string{"--wait", strconv.Itoa(wifiActivateSecs), "connection", "up", "id", name}
	if iface := h.wifiInterface(ctx); iface != "" {
		args = append(args, "ifname", iface)
	}
	if _, err := h.nmcli(ctx, args...); err != nil {
		if err := h.rollBackOrKeep(ctx, nil); err != nil {
			return nil, err
		}
		return nil, localrpc.Errorf(CodeNotJoined, "The device could not join this network (wrong password, or out of range). "+
			"The old Wi-Fi settings were put back.")
	}
	return WifiApplyReply{Applied: true, Connected: true}, nil
}

// rollBackOrKeep restores the pending change's old settings and returns cause.
// When the restore itself fails, the pending record stays for wifi.rollback
// and the restore error is returned instead.
func (h Host) rollBackOrKeep(ctx context.Context, cause error) error {
	p := h.readWifiPending()
	if p == nil {
		return cause
	}
	if err := h.restoreWifi(ctx, p); err != nil {
		return fmt.Errorf("the old Wi-Fi settings could not be put back yet (%v); the change stays pending", err)
	}
	h.clearWifiPending()
	return cause
}

func (h Host) wifiConfirm(_ context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	wifiMu.Lock()
	defer wifiMu.Unlock()
	h.clearWifiPending()
	return map[string]bool{"confirmed": true}, nil
}

func (h Host) wifiRollback(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	wifiMu.Lock()
	defer wifiMu.Unlock()
	p := h.readWifiPending()
	if p == nil {
		return WifiRollbackReply{}, nil
	}
	if err := h.restoreWifi(ctx, p); err != nil {
		return nil, fmt.Errorf("the old Wi-Fi settings could not be put back (%v); the change stays pending", err)
	}
	h.clearWifiPending()
	return WifiRollbackReply{RolledBack: true, WifiPending: WifiPending{ID: p.ID, Revision: p.Revision}}, nil
}

func (h Host) wifiRemove(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WifiRemoveRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := validSSID(r.SSID); err != nil {
		return nil, err
	}
	if err := h.requireWifi(ctx); err != nil {
		return nil, err
	}
	wifiMu.Lock()
	defer wifiMu.Unlock()
	var profiles []nmProfile
	for _, p := range h.wifiProfiles(ctx) {
		if p.ssid == r.SSID {
			profiles = append(profiles, p)
		}
	}
	if len(profiles) == 0 {
		return map[string]bool{"removed": true, "found": false}, nil
	}
	if current, _ := h.activeWifi(ctx); current == r.SSID && !h.wiredConnected(ctx) {
		return nil, localrpc.Errorf(CodeWouldCutOff, "The device is connected through this network and has no other connection. "+
			"Add another network first, or connect a cable.")
	}
	var generated []nmProfile
	for _, p := range profiles {
		if h.inConnectionsDir(p.file) {
			if _, err := h.nmcli(ctx, "connection", "delete", "uuid", p.uuid); err != nil && h.profileExists(ctx, p.uuid) {
				return nil, fmt.Errorf("NetworkManager did not delete %s: %w", p.name, err)
			}
		} else {
			generated = append(generated, p)
		}
	}
	if len(generated) > 0 {
		if !h.forgetInNetplan(ctx, generated, r.SSID) {
			return nil, localrpc.Errorf(CodeUnsupported, "This network was set up by the operating system's own configuration "+
				"(netplan), which this device recreates at boot, and it could not be removed from there.")
		}
		var kept []string
		for _, p := range h.wifiProfiles(ctx) {
			if p.ssid == r.SSID {
				kept = append(kept, p.name)
			}
		}
		if len(kept) > 0 {
			return nil, localrpc.Errorf(CodeUnsupported, "This network was set up by the operating system's own configuration "+
				"(%s), which this device recreates at boot, and it could not be removed from there.", strings.Join(kept, ", "))
		}
	}
	return map[string]bool{"removed": true, "found": true}, nil
}

// profileExists says NetworkManager still has a connection with this UUID.
func (h Host) profileExists(ctx context.Context, uuid string) bool {
	for _, p := range h.wifiProfiles(ctx) {
		if p.uuid == uuid {
			return true
		}
	}
	return false
}

// auditWifi names the network, never the password.
func auditWifi(raw json.RawMessage) []any {
	var r struct {
		SSID       string `json:"ssid"`
		ConnectNow bool   `json:"connect_now"`
	}
	_ = json.Unmarshal(raw, &r)
	return []any{"ssid", r.SSID, "connect_now", r.ConnectNow}
}
