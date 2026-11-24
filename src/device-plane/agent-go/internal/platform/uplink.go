package platform

import (
	"os"
	"strconv"
	"strings"
)

// rtfUp is the "route is up" flag in /proc/net/route.
const rtfUp = 0x1

// Uplink is the link this device's traffic leaves on, reported with the Wi-Fi
// state (network-wifi-report.request.json: uplink_type, uplink_interface).
type Uplink struct {
	// Type is ethernet, wifi, cellular or other.
	Type string `json:"uplink_type,omitempty"`
	// Interface is the network interface, e.g. eth0.
	Interface string `json:"uplink_interface,omitempty"`
}

// Uplink returns the interface of the IPv4 default route with the lowest
// metric and its kind, read from /proc and sysfs (no root, no NetworkManager).
// The zero value means there is no default route.
func (h *Host) Uplink() Uplink {
	best, bestMetric := "", -1
	lines := strings.Split(h.readText("/proc/net/route"), "\n")
	for _, line := range lines[min(1, len(lines)):] {
		f := strings.Fields(line)
		if len(f) < 8 || f[1] != "00000000" || f[7] != "00000000" {
			continue
		}
		flags, err1 := strconv.ParseInt(f[3], 16, 64)
		metric, err2 := strconv.Atoi(f[6])
		if err1 != nil || err2 != nil || flags&rtfUp == 0 {
			continue
		}
		if bestMetric < 0 || metric < bestMetric {
			best, bestMetric = f[0], metric
		}
	}
	if best == "" {
		return Uplink{}
	}
	iface := best
	if len(iface) > 32 {
		iface = iface[:32]
	}
	return Uplink{Type: h.interfaceKind(best), Interface: iface}
}

// interfaceKind is the Python agent's _iface_kind: ethernet, wifi, cellular or other.
func (h *Host) interfaceKind(iface string) string {
	base := "/sys/class/net/" + iface
	devtype := ""
	for _, line := range strings.Split(h.readText(base+"/uevent"), "\n") {
		if v, ok := strings.CutPrefix(line, "DEVTYPE="); ok {
			devtype = v
			break
		}
	}
	switch {
	case devtype == "wlan" || h.exists(base+"/wireless") || h.exists(base+"/phy80211"):
		return "wifi"
	case devtype == "wwan" || strings.HasPrefix(iface, "wwan"):
		return "cellular"
	case isVirtual(iface) || devtype != "":
		return "other"
	}
	// ARPHRD_ETHER on real hardware (a "device" link); bridges and tunnels have none.
	if h.readText(base+"/type") == "1" && h.exists(base+"/device") {
		return "ethernet"
	}
	return "other"
}

// SystemUplink reads the uplink of the real machine (the root helper uses it).
func SystemUplink() Uplink {
	h := &Host{}
	if _, err := os.Stat("/proc/net/route"); err != nil {
		return Uplink{}
	}
	return h.Uplink()
}
