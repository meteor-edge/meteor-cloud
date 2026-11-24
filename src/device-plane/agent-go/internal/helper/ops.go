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

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
	"meteorcloud/meteorcli/internal/platform"
)

// maxFollow bounds a followed log (the console's Logs window).
const maxFollow = 24 * time.Hour

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
	// StreamAll is Stream with stderr in the same writer (docker logs prints
	// a container's stderr there).
	StreamAll func(ctx context.Context, w io.Writer, name string, args ...string) error
	// RunInput is Run with stdin (secrets such as passwords go here, never in args).
	RunInput func(ctx context.Context, stdin []byte, name string, args ...string) ([]byte, error)
	// LookupUser finds an account; it fails with errNoUser for an unknown one.
	LookupUser func(name string) (UserInfo, error)
	// HomeDir is where useradd -m puts name's home; nil means /home/<name>.
	HomeDir func(name string) string
	// Lstat is like os.Lstat; nil means os.Lstat. Tests use it for home probes.
	Lstat func(path string) (os.FileInfo, error)
	// UnitDir is where systemd.install_unit writes units.
	UnitDir string
	// SudoersDir is where users.add writes a sudo rule.
	SudoersDir string
	// ArtifactsDir, ComposeDir and AppCredentialsDir are the agent's directories
	// the deploy operations may read files from (docker.load, compose.*, docker.run).
	ArtifactsDir      string
	ComposeDir        string
	AppCredentialsDir string
	// TempDir is where docker.run writes its private env file ("" for the
	// helper's own PrivateTmp /tmp).
	TempDir string
	// LogGlobs are the log files that may be read (nil: the /var/log defaults).
	LogGlobs []string
	// SystemdDir and DockerSocket say whether the journal and Docker exist
	// ("" for /run/systemd/system and /var/run/docker.sock).
	SystemdDir, DockerSocket string
	// StateDir is the helper's own state (root 0700): the Wi-Fi change waiting
	// for confirm and the WireGuard config.
	StateDir string
	// NMConnectionsDir is NetworkManager's keyfile directory.
	NMConnectionsDir string
	// SysNetDir is /sys/class/net ("" for the real one).
	SysNetDir string
	// ConfigFile is config.json, read for ssh_user ("" for /etc/meteorcli/config.json).
	ConfigFile string
	// LookNetplan says netplan is installed; nil means look in the safe PATH.
	LookNetplan func() bool
	// Uplink reads the link traffic leaves on; nil means the real machine.
	Uplink func() platform.Uplink
	// UpdaterBin is the meteorcli update.start runs as the updater ("" for
	// /usr/local/bin/meteorcli).
	UpdaterBin string
}

// SystemHost runs real tools from safePath and writes to /etc/systemd/system.
func SystemHost() Host {
	p := config.DefaultPaths("")
	return Host{
		Run: runTool, Stream: streamTool, StreamAll: streamToolAll, RunInput: runToolInput, LookupUser: lookupUser,
		UnitDir: "/etc/systemd/system", SudoersDir: "/etc/sudoers.d",
		StateDir: "/var/lib/meteorcli-helper", NMConnectionsDir: "/etc/NetworkManager/system-connections",
		ArtifactsDir: p.ArtifactsDir, ComposeDir: p.ComposeDir(), AppCredentialsDir: p.AppCredentialsDir(),
	}
}

// dockerConfigDir is where an operator puts registry credentials for the
// deploy operations (docker login --config /etc/meteorcli/docker ...): the
// helper cannot see /root.
const dockerConfigDir = "/etc/meteorcli/docker"

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
	if name == "docker" {
		cmd.Env = append(cmd.Env, "DOCKER_CONFIG="+dockerConfigDir)
	}
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

func runToolInput(ctx context.Context, stdin []byte, name string, args ...string) ([]byte, error) {
	cmd, err := command(ctx, name, args)
	if err != nil {
		return nil, err
	}
	out := &capped{max: maxOutput}
	cmd.Stdin, cmd.Stdout, cmd.Stderr = bytes.NewReader(stdin), out, out
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

func streamToolAll(ctx context.Context, w io.Writer, name string, args ...string) error {
	cmd, err := command(ctx, name, args)
	if err != nil {
		return err
	}
	cmd.Stdout, cmd.Stderr = w, w
	return finish(ctx, cmd.Run(), name, args, "")
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
		// `meteorcli doctor`: the helper is reachable and accepts the agent.
		"ping":                 {Timeout: 5 * time.Second, Run: h.ping},
		"power.reboot":         {Timeout: 30 * time.Second, Run: h.powerReboot},
		"power.shutdown":       {Timeout: 30 * time.Second, Run: h.powerShutdown},
		"logs.read":            {Timeout: 30 * time.Second, Run: h.logsRead, Audit: auditLogs},
		"systemd.install_unit": {Timeout: 2 * time.Minute, Run: h.installUnit, Audit: auditInstallUnit},
		"users.add":            {Timeout: time.Minute, Run: h.addUser, Audit: auditUser},
		"users.set_password":   {Timeout: 30 * time.Second, Run: h.setPassword, Audit: auditUser},
		"docker.ps":            {Timeout: 30 * time.Second, Run: h.dockerPS},
		"docker.inspect":       {Timeout: 30 * time.Second, Run: h.dockerInspect},
		"docker.stats":         {Timeout: 30 * time.Second, Run: h.dockerStats},
		"logs.warnings":        {Timeout: 30 * time.Second, Run: h.logsWarnings},
		// Deploys (internal/deploy). docker.run and compose.up may pull images.
		"systemd.control":  {Timeout: 2 * time.Minute, Run: h.systemdControl, Audit: auditSystemdControl},
		"docker.load":      {Timeout: deployTimeout, Run: h.dockerLoad},
		"docker.run":       {Timeout: deployTimeout, Run: h.dockerRunContainer, Audit: auditDockerRun},
		"docker.health":    {Timeout: 30 * time.Second, Run: h.dockerHealth},
		"docker.remove":    {Timeout: 2 * time.Minute, Run: h.dockerRemove, Audit: auditDockerName},
		"compose.services": {Timeout: time.Minute, Run: h.composeServices, Audit: auditCompose},
		"compose.up":       {Timeout: deployTimeout, Run: h.composeUp, Audit: auditCompose},
		"compose.ps":       {Timeout: 30 * time.Second, Run: h.composePS},
		"compose.down":     {Timeout: 5 * time.Minute, Run: h.composeDown, Audit: auditCompose},
		// Looking at and managing deployed workloads (workload_manage.go).
		"workload.inspect": {Timeout: time.Minute, Run: h.workloadInspect},
		"workload.control": {Timeout: 2 * time.Minute, Run: h.workloadControl, Audit: auditWorkloadControl},
		// Logs for `meteorcli logs` and the console (internal/helper/logsources.go).
		"logs.list": {Timeout: time.Minute, Run: h.logsList},
		"logs.show": {Timeout: maxFollow, Stream: h.logsShow, Audit: auditShow},
		// Network (internal/network): Wi-Fi through NetworkManager, wg0, SSH keys.
		"wifi.status":         {Timeout: time.Minute, Run: h.wifiStatus},
		"wifi.apply":          {Timeout: 3 * time.Minute, Run: h.wifiApply, Audit: auditWifi},
		"wifi.confirm":        {Timeout: 30 * time.Second, Run: h.wifiConfirm},
		"wifi.rollback":       {Timeout: 2 * time.Minute, Run: h.wifiRollback},
		"wifi.remove":         {Timeout: 2 * time.Minute, Run: h.wifiRemove, Audit: auditWifi},
		"wireguard.apply":     {Timeout: time.Minute, Run: h.wireguardApply, Audit: auditWireGuard},
		"wireguard.down":      {Timeout: 30 * time.Second, Run: h.wireguardDown},
		"wireguard.show":      {Timeout: 30 * time.Second, Run: h.wireguardShow},
		"wireguard.endpoint":  {Timeout: 30 * time.Second, Run: h.wireguardEndpoint},
		"ssh.authorized_keys": {Timeout: 30 * time.Second, Run: h.sshAuthorizedKeys, Audit: auditSSHKeys},
		// The updater (internal/update) in its own transient unit.
		"update.start": {Timeout: 30 * time.Second, Run: h.updateStart, Audit: auditUpdate},
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

// ping answers that the helper runs and accepted the caller; it does nothing else.
func (h Host) ping(_ context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	return map[string]bool{"ok": true}, nil
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
