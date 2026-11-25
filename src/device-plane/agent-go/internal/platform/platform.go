// Package platform reads facts about the machine the agent runs on: OS and
// board, CPU, memory, network interfaces, temperatures, and which tools are
// installed. It is a port of the Python agent's edge_agent/platform, inventory
// and metrics modules. Every reading is best effort: a file or tool that is
// missing gives "unknown", never an error, so the agent runs on unusual systems.
//
// Device types (Raspberry Pi, NUC, Jetson, ...) are catalog rows in the control
// plane, not separate code. When a board needs a different reading, add a Board
// to the boards table below; nothing else changes.
package platform

import (
	"context"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

// Host reads one machine. The zero value is not usable; call System.
// Tests build a Host with Root pointing at a fake /proc, /sys and /etc tree.
type Host struct {
	// Root prefixes every file read ("" on a device).
	Root string
	// Hostname returns the host name.
	Hostname func() (string, error)
	// Uname returns the kernel release and machine (uname -r, uname -m).
	Uname func() (release, machine string)
	// LookPath finds a tool on PATH.
	LookPath func(name string) (string, error)
	// Run runs a tool with an argument list and reports whether it exited 0.
	// It must honour ctx (the caller sets a timeout).
	Run func(ctx context.Context, name string, args ...string) bool
	// IPv4 returns the IPv4 addresses of a network interface.
	IPv4 func(iface string) []net.IP
	// Statfs returns the size, the free space, and the space available to
	// normal users (free minus the root reserve) of the filesystem holding
	// path, in bytes.
	Statfs func(path string) (total, free, avail uint64, ok bool)

	board *Board
}

// System returns a Host that reads the real machine.
func System() *Host {
	h := &Host{
		Hostname: os.Hostname,
		Uname:    uname,
		LookPath: exec.LookPath,
		Run:      runQuiet,
		IPv4:     interfaceIPv4,
		Statfs:   statfs,
	}
	h.board = Detect(h)
	return h
}

// Board describes a family of boards that needs readings of its own.
type Board struct {
	// Name identifies the board family in logs ("raspberry-pi").
	Name string
	// Match reports whether h is this kind of board. The generic board matches all.
	Match func(h *Host) bool
	// ThermalZones lists /sys/class/thermal zone types to prefer for the SoC
	// temperature, best first. thermal_zone0 is always the last resort.
	ThermalZones []string
}

// boards is checked in order; the last entry matches every machine. Add a board
// here when one needs different readings.
var boards = []Board{
	{
		Name:         "raspberry-pi",
		Match:        func(h *Host) bool { return strings.HasPrefix(h.HardwareModel(), "Raspberry Pi") },
		ThermalZones: []string{"cpu-thermal"},
	},
	{
		Name:         "nvidia-jetson",
		Match:        func(h *Host) bool { return strings.Contains(h.HardwareModel(), "Jetson") },
		ThermalZones: []string{"cpu-thermal", "soc-thermal", "cpu"},
	},
	{
		// Same zone order as the Python agent: one metrics key works across boards.
		Name:         "generic-linux",
		Match:        func(*Host) bool { return true },
		ThermalZones: []string{"cpu-thermal", "x86_pkg_temp", "soc-thermal", "cpu"},
	},
}

// Detect returns the first board in the table that matches h.
func Detect(h *Host) *Board {
	for i := range boards {
		if boards[i].Match(h) {
			return &boards[i]
		}
	}
	return &boards[len(boards)-1]
}

// Board returns the detected board family.
func (h *Host) Board() *Board {
	if h.board == nil {
		h.board = Detect(h)
	}
	return h.board
}

// path maps an absolute path into Root.
func (h *Host) path(p string) string { return filepath.Join(h.Root, p) }

// readText reads a text or device-tree file. Device-tree values end in NUL, so
// NUL bytes are dropped. A missing or empty file returns "".
func (h *Host) readText(p string) string {
	data, err := os.ReadFile(h.path(p))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(strings.ReplaceAll(string(data), "\x00", ""))
}

func uname() (release, machine string) {
	var u syscall.Utsname
	if err := syscall.Uname(&u); err != nil {
		return "", ""
	}
	return utsString(u.Release[:]), utsString(u.Machine[:])
}

// utsString converts a NUL-terminated utsname field (int8 or uint8 per arch).
func utsString[T int8 | uint8](field []T) string {
	b := make([]byte, 0, len(field))
	for _, c := range field {
		if c == 0 {
			break
		}
		b = append(b, byte(c))
	}
	return string(b)
}

func runQuiet(ctx context.Context, name string, args ...string) bool {
	return exec.CommandContext(ctx, name, args...).Run() == nil
}

func interfaceIPv4(name string) []net.IP {
	iface, err := net.InterfaceByName(name)
	if err != nil {
		return nil
	}
	addrs, err := iface.Addrs()
	if err != nil {
		return nil
	}
	var out []net.IP
	for _, a := range addrs {
		if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil {
			out = append(out, n.IP.To4())
		}
	}
	return out
}

func statfs(path string) (total, free, avail uint64, ok bool) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return 0, 0, 0, false
	}
	bsize := uint64(st.Frsize)
	if bsize == 0 {
		bsize = uint64(st.Bsize)
	}
	return st.Blocks * bsize, st.Bfree * bsize, st.Bavail * bsize, true
}
