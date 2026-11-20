package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
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

// maxOutput bounds what Run keeps of a tool's output.
const maxOutput = 1 << 20

// Host is what operations act on. SystemHost is the real machine; tests use a fake.
type Host struct {
	// Run executes a tool with an argument list (never a shell) and returns up to
	// 1 MiB of its combined output. It must honour ctx.
	Run func(ctx context.Context, name string, args ...string) ([]byte, error)
	// Stream executes a tool like Run but writes its stdout to w as it comes.
	Stream func(ctx context.Context, w io.Writer, name string, args ...string) error
	// UnitDir is where systemd.install_unit writes units.
	UnitDir string
}

// SystemHost runs real tools from safePath and writes to /etc/systemd/system.
func SystemHost() Host {
	return Host{Run: runTool, Stream: streamTool, UnitDir: "/etc/systemd/system"}
}

// lookTool finds name in safePath only, never in the helper's own PATH.
func lookTool(name string) (string, error) {
	if strings.ContainsRune(name, '/') {
		return "", fmt.Errorf("tool %q must be a bare name", name)
	}
	for _, dir := range filepath.SplitList(safePath) {
		path := filepath.Join(dir, name)
		if st, err := os.Stat(path); err == nil && st.Mode().IsRegular() && st.Mode()&0o111 != 0 {
			return path, nil
		}
	}
	return "", fmt.Errorf("%s not found in %s", name, safePath)
}

func command(ctx context.Context, name string, args []string) (*exec.Cmd, error) {
	path, err := lookTool(name)
	if err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Env = []string{"PATH=" + safePath, "LANG=C.UTF-8"}
	return cmd, nil
}

func runTool(ctx context.Context, name string, args ...string) ([]byte, error) {
	cmd, err := command(ctx, name, args)
	if err != nil {
		return nil, err
	}
	out := &capped{max: maxOutput}
	cmd.Stdout, cmd.Stderr = out, out
	err = finish(ctx, cmd.Run(), name, args, out.String())
	return out.Bytes(), err
}

func streamTool(ctx context.Context, w io.Writer, name string, args ...string) error {
	cmd, err := command(ctx, name, args)
	if err != nil {
		return err
	}
	stderr := &capped{max: 4 << 10}
	cmd.Stdout, cmd.Stderr = w, stderr
	return finish(ctx, cmd.Run(), name, args, stderr.String())
}

func finish(ctx context.Context, err error, name string, args []string, out string) error {
	if ctx.Err() != nil {
		return fmt.Errorf("%s: %w", name, ctx.Err())
	}
	if err != nil {
		return fmt.Errorf("%s %s: %w: %s", name, strings.Join(args, " "), err, firstLine(out))
	}
	return nil
}

// capped keeps the first max bytes written and drops the rest (the tool keeps running).
type capped struct {
	bytes.Buffer
	max int
}

func (c *capped) Write(p []byte) (int, error) {
	if room := c.max - c.Len(); room > 0 {
		c.Buffer.Write(p[:min(len(p), room)])
	}
	return len(p), nil
}

// Ops is the allowlist: every operation the helper performs. Add an operation:
// a function in its own file and one line here.
func Ops(h Host) map[string]localrpc.Handler {
	return map[string]localrpc.Handler{
		"power.reboot":         {Timeout: 30 * time.Second, Run: h.powerReboot},
		"power.shutdown":       {Timeout: 30 * time.Second, Run: h.powerShutdown},
		"logs.read":            {Timeout: 30 * time.Second, Run: h.logsRead, Audit: auditLogs},
		"systemd.install_unit": {Timeout: 2 * time.Minute, Run: h.installUnit, Audit: auditInstallUnit},
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
