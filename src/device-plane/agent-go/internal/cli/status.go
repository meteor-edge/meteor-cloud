package cli

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// Answers of presence; the CLI only stats secret files, it never reads them.
const (
	present = "present"
	missing = "MISSING"
	denied  = "unknown (permission denied; use sudo)"
)

// serviceUnit is the agent's systemd unit.
const serviceUnit = "meteorcli.service"

// runStatus is `meteorcli status`: the same lines as the Python CLI. It exits 1
// until the device is enrolled. Until the agent socket exists it reads the files
// directly, so enrollment details need root or the meteor user.
func runStatus(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "status", "")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	p := env.Paths
	out := env.Stdout

	// The running agent knows best and can read what this user may not.
	var reply agent.StatusReply
	err := localrpc.Call(ctx, p.AgentSocket(), "status", nil, &reply)
	switch localrpc.CodeOf(err) {
	case "":
		if err == nil {
			return printAgentStatus(ctx, env, reply)
		}
		fmt.Fprintf(env.Stderr, "error: agent: %v\n", err)
		return 1
	case localrpc.CodeDenied:
		fmt.Fprintf(env.Stderr, "error: %s\n", deniedMessage(err))
		return 1
	case localrpc.CodeUnavailable:
		// Not running: read the files, as far as this user may.
	default:
		fmt.Fprintf(env.Stderr, "error: agent: %v\n", err)
		return 1
	}
	fmt.Fprintln(out, "Agent:          not running (details read from files)")

	// The Python config.json sits where the new one goes; look for it first.
	if config.LooksLikePython(p.LegacyDir) {
		fmt.Fprintf(out, "Found a Python meteorcli install in %s. Run 'sudo meteorcli config import'.\n", p.LegacyDir)
		return 1
	}
	c, err := config.Load(p.ConfigFile)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
	}
	if c == nil && presence(p.APIKeyFile()) == missing {
		fmt.Fprintln(out, "Not configured. Run 'meteorcli config --domain … --api-key …' first.")
		return 1
	}

	device, derr := config.LoadDevice(p.DeviceFile())
	deviceValue := func(get func(*config.Device) string) string {
		switch {
		case device != nil:
			return orDash(get(device))
		case errors.Is(derr, fs.ErrPermission):
			return denied
		default:
			return "—"
		}
	}
	fmt.Fprintf(out, "Domain:         %s\n", orDash(fieldOf(c, func(c *config.Config) string { return c.Domain })))
	fmt.Fprintf(out, "API base:       %s\n", orDash(c.Server()))
	fmt.Fprintf(out, "API key:        %s\n", presence(p.APIKeyFile()))
	fmt.Fprintf(out, "Device ID:      %s\n", deviceValue(func(d *config.Device) string { return d.DeviceID }))
	fmt.Fprintf(out, "Organization:   %s\n", deviceValue(func(d *config.Device) string { return d.OrganizationID }))
	fmt.Fprintf(out, "Name:           %s\n", deviceValue(func(d *config.Device) string { return d.Name }))
	fmt.Fprintf(out, "Heartbeat:      every %ds\n", int(device.Heartbeat().Seconds()))
	fmt.Fprintf(out, "Credential:     %s\n", presence(p.DeviceTokenFile()))
	fmt.Fprintf(out, "Service:        %s is %s\n", serviceUnit, env.ServiceState(ctx))
	fmt.Fprintf(out, "MQTT:           %s\n", configured(presence(p.MQTTFile())))
	fmt.Fprintf(out, "WireGuard:      %s\n", wireguardSummary(p))
	if presence(p.PendingClaimFile()) == present {
		fmt.Fprintln(out, "Pending claim:  waiting for an operator to approve this device")
	}
	if device == nil || device.DeviceID == "" {
		return 1
	}
	return 0
}

// printAgentStatus prints the agent's answer with the same lines as the Python CLI.
func printAgentStatus(ctx context.Context, env *Env, r agent.StatusReply) int {
	out := env.Stdout
	yes := func(b bool, y, n string) string {
		if b {
			return y
		}
		return n
	}
	fmt.Fprintf(out, "Agent:          running %s (%s)\n", r.Version, strings.ReplaceAll(r.State, "_", " "))
	fmt.Fprintf(out, "Domain:         %s\n", orDash(r.Domain))
	fmt.Fprintf(out, "API base:       %s\n", orDash(r.Server))
	fmt.Fprintf(out, "API key:        %s\n", yes(r.APIKey, present, missing))
	fmt.Fprintf(out, "Device ID:      %s\n", orDash(r.DeviceID))
	fmt.Fprintf(out, "Organization:   %s\n", orDash(r.OrganizationID))
	fmt.Fprintf(out, "Name:           %s\n", orDash(r.Name))
	fmt.Fprintf(out, "Heartbeat:      every %ds\n", r.HeartbeatSeconds)
	fmt.Fprintf(out, "Credential:     %s\n", yes(r.Credential, present, missing))
	fmt.Fprintf(out, "Service:        %s is %s\n", serviceUnit, env.ServiceState(ctx))
	fmt.Fprintf(out, "MQTT:           %s\n", yes(r.MQTT, "configured", "not configured"))
	fmt.Fprintf(out, "WireGuard:      %s\n", yes(r.WireGuard, "key pair present", "not set up"))
	if r.PendingClaim {
		fmt.Fprintf(out, "Pending claim:  request %s is waiting for an operator to approve this device\n", orDash(r.PendingRequestID))
	}
	names := make([]string, 0, len(r.Modules))
	for name := range r.Modules {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		h := r.Modules[name]
		line := fmt.Sprintf("Module %-8s %s, %d restarts", name+":", h.State, h.Restarts)
		if h.LastError != nil {
			line += ", last error: " + *h.LastError
		}
		fmt.Fprintln(out, line)
	}
	if r.State != agent.StateEnrolled {
		return 1
	}
	return 0
}

// deniedMessage explains a refused socket call. agent.sock belongs to the
// meteorcli group, so an administrator needs both groups.
func deniedMessage(err error) string {
	return fmt.Sprintf("%v\nAsk an administrator to add you to the %s group to read status and logs,\n"+
		"and also to the %s group to manage the device; then log in again.",
		err, agent.GroupRead, agent.GroupAdmin)
}

// presence says whether a file exists without opening it.
func presence(path string) string {
	_, err := os.Stat(path)
	switch {
	case err == nil:
		return present
	case errors.Is(err, fs.ErrPermission):
		return denied
	default:
		return missing
	}
}

func configured(p string) string {
	switch p {
	case present:
		return "configured"
	case missing:
		return "not configured"
	default:
		return p
	}
}

func wireguardSummary(p config.Paths) string {
	switch presence(filepath.Join(p.WireGuardDir(), "public.key")) {
	case present:
		return "key pair present"
	case missing:
		return "not set up"
	default:
		return denied
	}
}

// systemctlState asks systemd for the unit's state. It never fails: errors
// read as "unknown" and a missing unit as "not installed", like the Python CLI.
func systemctlState(ctx context.Context) string {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "systemctl", "show", "-p", "LoadState", "-p", "ActiveState", "--", serviceUnit).Output()
	if err != nil {
		return "unknown"
	}
	return parseSystemctlShow(string(out))
}

func parseSystemctlShow(out string) string {
	var load, active string
	for _, line := range strings.Split(out, "\n") {
		k, v, _ := strings.Cut(strings.TrimSpace(line), "=")
		switch k {
		case "LoadState":
			load = v
		case "ActiveState":
			active = v
		}
	}
	if load == "not-found" || load == "" {
		return "not installed"
	}
	if active == "" {
		return "unknown"
	}
	return active
}
