package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
)

// safePath is the only PATH the tools run with.
const safePath = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

// validName is the one rule for names that come from the agent (units, ...).
// It cannot start with "-", so a name is never read as an option.
var validName = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,62}$`)

// Host is what operations act on. SystemHost is the real machine; tests use a fake.
type Host struct {
	// Run executes a tool with an argument list (never a shell) and returns its
	// combined output. It must honour ctx.
	Run func(ctx context.Context, name string, args ...string) ([]byte, error)
	// UnitDir is where systemd.install_unit writes units.
	UnitDir string
}

// SystemHost runs real tools with a fixed PATH and writes to /etc/systemd/system.
func SystemHost() Host {
	return Host{Run: runTool, UnitDir: "/etc/systemd/system"}
}

func runTool(ctx context.Context, name string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = []string{"PATH=" + safePath, "LANG=C.UTF-8"}
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err := cmd.Run()
	if ctx.Err() != nil {
		return out.Bytes(), fmt.Errorf("%s: %w", name, ctx.Err())
	}
	if err != nil {
		return out.Bytes(), fmt.Errorf("%s %s: %w: %s", name, strings.Join(args, " "), err, firstLine(out.String()))
	}
	return out.Bytes(), nil
}

// Ops is the allowlist: every operation the helper performs. Add an operation:
// a function in its own file and one line here.
func Ops(h Host) map[string]localrpc.Handler {
	return map[string]localrpc.Handler{
		"power.reboot":         {Timeout: 30 * time.Second, Run: h.powerReboot},
		"power.shutdown":       {Timeout: 30 * time.Second, Run: h.powerShutdown},
		"logs.read":            {Timeout: 30 * time.Second, Run: h.logsRead},
		"systemd.install_unit": {Timeout: 2 * time.Minute, Run: h.installUnit},
	}
}

// decode reads params into v and rejects unknown fields: a typo must not be
// silently ignored by a root process.
func decode(raw json.RawMessage, v any) error {
	if len(raw) == 0 {
		raw = json.RawMessage("{}")
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return localrpc.Errorf(localrpc.CodeBadRequest, "params: %v", err)
	}
	return nil
}

// Started is the answer of operations that only start something.
type Started struct {
	Started bool `json:"started"`
}

func (h Host) powerReboot(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	// --no-block queues the job and returns, so the agent gets its answer (and can
	// report "done") before the system goes down, like the Python helper.
	if _, err := h.Run(ctx, "systemctl", "--no-block", "reboot"); err != nil {
		return nil, err
	}
	return Started{Started: true}, nil
}

func (h Host) powerShutdown(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	if _, err := h.Run(ctx, "systemctl", "--no-block", "poweroff"); err != nil {
		return nil, err
	}
	return Started{Started: true}, nil
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if len(s) > 300 {
		s = s[:300]
	}
	return s
}
