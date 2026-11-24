package platform

import (
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
)

// OSRelease returns the OS name and version from /etc/os-release ("" when unknown).
func (h *Host) OSRelease() (name, version string) {
	values := map[string]string{}
	for _, line := range strings.Split(h.readText("/etc/os-release"), "\n") {
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		values[strings.TrimSpace(k)] = osReleaseValue(strings.TrimSpace(v))
	}
	return firstNonEmpty(values["NAME"], values["PRETTY_NAME"], values["ID"]),
		firstNonEmpty(values["VERSION_ID"], values["VERSION"])
}

// osReleaseValue decodes one os-release value: unquoted, or in single or
// double quotes. A backslash escapes $ " ` \ in double quotes and any
// character in an unquoted value. The file is never executed.
func osReleaseValue(v string) string {
	if len(v) >= 2 && v[0] == '\'' && v[len(v)-1] == '\'' {
		return v[1 : len(v)-1]
	}
	quoted := len(v) >= 2 && v[0] == '"' && v[len(v)-1] == '"'
	if quoted {
		v = v[1 : len(v)-1]
	}
	var b strings.Builder
	for i := 0; i < len(v); i++ {
		if v[i] == '\\' && i+1 < len(v) && (!quoted || strings.IndexByte("$\"`\\", v[i+1]) >= 0) {
			i++
		}
		b.WriteByte(v[i])
	}
	return b.String()
}

// placeholders are firmware values that identify nothing.
var placeholders = map[string]bool{
	"default string": true, "none": true, "not applicable": true, "not specified": true,
	"system product name": true, "system serial number": true, "to be filled by o.e.m.": true,
	"unknown": true,
}

// realValue drops firmware placeholders and all-zero serials.
func realValue(v string) string {
	v = strings.TrimSpace(strings.ReplaceAll(v, "\x00", ""))
	if v == "" || placeholders[strings.ToLower(v)] || strings.Trim(v, "0") == "" {
		return ""
	}
	return v
}

func (h *Host) firstReal(paths ...string) string {
	for _, p := range paths {
		if v := realValue(h.readText(p)); v != "" {
			return v
		}
	}
	return ""
}

// cpuinfo returns the key/value lines of /proc/cpuinfo, keys lower case.
func (h *Host) cpuinfo() [][2]string {
	var out [][2]string
	for _, line := range strings.Split(h.readText("/proc/cpuinfo"), "\n") {
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		out = append(out, [2]string{strings.ToLower(strings.TrimSpace(k)), strings.TrimSpace(v)})
	}
	return out
}

// SerialNumber returns the board serial. 64-bit Raspberry Pi kernels leave it
// out of /proc/cpuinfo but the device tree has it; PCs expose it through DMI
// (product_serial is readable by root only, so the unprivileged agent usually
// gets it from board_serial or not at all).
func (h *Host) SerialNumber() string {
	for _, kv := range h.cpuinfo() {
		if kv[0] == "serial" {
			if v := realValue(kv[1]); v != "" {
				return v
			}
			break
		}
	}
	return h.firstReal(
		"/sys/firmware/devicetree/base/serial-number",
		"/proc/device-tree/serial-number",
		"/sys/class/dmi/id/product_serial",
		"/sys/class/dmi/id/board_serial",
	)
}

// HardwareModel returns the board model ("Raspberry Pi 5 Model B Rev 1.0") or
// the DMI product name.
func (h *Host) HardwareModel() string {
	return h.firstReal(
		"/sys/firmware/devicetree/base/model",
		"/sys/class/dmi/id/product_name",
		"/sys/class/dmi/id/board_name",
	)
}

var (
	virtualPrefixes = []string{"lo", "docker", "veth", "br-", "cni", "flannel", "virbr", "wg", "tun", "tap"}
	wiredPrefixes   = []string{"eth", "en", "end"}
	wifiPrefixes    = []string{"wlan", "wl"}
)

func hasAnyPrefix(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

func isVirtual(iface string) bool { return iface == "lo" || hasAnyPrefix(iface, virtualPrefixes) }

// preference orders interfaces: wired, then Wi-Fi, then the rest.
func preference(iface string) int {
	switch {
	case hasAnyPrefix(iface, wiredPrefixes):
		return 0
	case hasAnyPrefix(iface, wifiPrefixes):
		return 1
	default:
		return 2
	}
}

// physicalInterfaces lists non-virtual interfaces in preference order.
func (h *Host) physicalInterfaces() []string {
	entries, err := os.ReadDir(h.path("/sys/class/net"))
	if err != nil {
		return nil
	}
	var names []string
	for _, e := range entries {
		if !isVirtual(e.Name()) {
			names = append(names, e.Name())
		}
	}
	sort.Slice(names, func(i, j int) bool {
		pi, pj := preference(names[i]), preference(names[j])
		if pi != pj {
			return pi < pj
		}
		return names[i] < names[j]
	})
	return names
}

// MACAddresses returns the primary NIC's MAC (at most one). Routes are not used:
// this MAC identifies the device, so it must not change when traffic moves from
// Ethernet to Wi-Fi.
func (h *Host) MACAddresses() []string {
	for _, iface := range h.physicalInterfaces() {
		mac := h.readText("/sys/class/net/" + iface + "/address")
		if mac != "" && mac != "00:00:00:00:00:00" {
			return []string{mac}
		}
	}
	return []string{}
}

// LocalIPv4 returns the LAN address of the primary physical interface (wired,
// then Wi-Fi). Loopback, link-local, containers, bridges and WireGuard are skipped.
func (h *Host) LocalIPv4() string {
	for _, iface := range h.physicalInterfaces() {
		for _, ip := range h.IPv4(iface) {
			if ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() {
				continue
			}
			return ip.String()
		}
	}
	return ""
}

// armParts maps ARM Ltd MIDR part numbers to names. 64-bit Pi kernels have no
// "model name" in /proc/cpuinfo; they report only these codes.
var armParts = map[string]string{
	"0xd03": "ARM Cortex-A53", "0xd04": "ARM Cortex-A35", "0xd05": "ARM Cortex-A55",
	"0xd07": "ARM Cortex-A57", "0xd08": "ARM Cortex-A72", "0xd09": "ARM Cortex-A73",
	"0xd0a": "ARM Cortex-A75", "0xd0b": "ARM Cortex-A76", "0xd0d": "ARM Cortex-A77",
	"0xd41": "ARM Cortex-A78", "0xd42": "ARM Cortex-A78AE", "0xd47": "ARM Cortex-A710",
}

// CPU returns the CPU model and core count (0 when unknown). x86 has "model
// name"; aarch64 boards usually only "CPU part", named from armParts.
func (h *Host) CPU() (model string, cores int) {
	var name, hardware, boardModel, part, implementer string
	for _, kv := range h.cpuinfo() {
		k, v := kv[0], kv[1]
		if k == "processor" {
			cores++
		}
		if v == "" {
			continue
		}
		switch {
		case k == "model name" && name == "":
			name = v
		case k == "hardware" && hardware == "":
			hardware = v
		case k == "model" && boardModel == "":
			boardModel = v
		case k == "cpu part" && part == "":
			part = v
		case k == "cpu implementer" && implementer == "":
			implementer = v
		}
	}
	if name == "" {
		name = firstNonEmpty(armPartName(part, implementer), boardModel, hardware, part)
	}
	if cores == 0 {
		cores = runtime.NumCPU()
	}
	return name, cores
}

func armPartName(part, implementer string) string {
	if part == "" || (implementer != "" && strings.ToLower(implementer) != "0x41") {
		return ""
	}
	key := strings.ToLower(part)
	if !strings.HasPrefix(key, "0x") {
		key = "0x" + key
	}
	return armParts[key]
}

// meminfo returns one /proc/meminfo value in kB.
func (h *Host) meminfo(key string) (uint64, bool) {
	for _, line := range strings.Split(h.readText("/proc/meminfo"), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == key+":" {
			n, err := strconv.ParseUint(fields[1], 10, 64)
			return n, err == nil
		}
	}
	return 0, false
}

// MemoryMB returns the total memory in MB (0 when unknown).
func (h *Host) MemoryMB() int {
	kb, _ := h.meminfo("MemTotal")
	return int(kb / 1024)
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}
