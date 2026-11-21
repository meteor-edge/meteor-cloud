package update

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
	"meteorcloud/meteorcli/internal/state"
)

// Timeouts of the systemctl calls. A restart of a Type=notify unit waits for
// READY=1 (or the unit's failure), bounded by its TimeoutStartSec (90 s).
const (
	restartTimeout = 2 * time.Minute
	queryTimeout   = 15 * time.Second
	probeTimeout   = 5 * time.Second
)

// Systemctl is System on the real machine.
type Systemctl struct{}

func systemctl(ctx context.Context, timeout time.Duration, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "systemctl", args...)
	cmd.Env = []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8"}
	out, err := cmd.CombinedOutput()
	if err != nil {
		// The first line says what failed; the rest is systemctl's "See
		// systemctl status ..." hint, which only clutters the console.
		first, _, _ := strings.Cut(strings.TrimSpace(string(out)), "\n")
		return "", fmt.Errorf("systemctl %s: %w: %s", strings.Join(args, " "), err, first)
	}
	return strings.TrimSpace(string(out)), nil
}

// Restart implements System.
func (Systemctl) Restart(ctx context.Context, unit string) error {
	_, err := systemctl(ctx, restartTimeout, "restart", "--", unit)
	return err
}

// Stop implements System.
func (Systemctl) Stop(ctx context.Context, unit string) error {
	_, err := systemctl(ctx, restartTimeout, "stop", "--", unit)
	return err
}

// Unit implements System with one `systemctl show`.
func (Systemctl) Unit(ctx context.Context, unit string) (UnitState, error) {
	out, err := systemctl(ctx, queryTimeout, "show", "--property=ActiveState,NRestarts,InvocationID", "--", unit)
	if err != nil {
		return UnitState{}, err
	}
	return parseShow(out)
}

// parseShow reads `systemctl show` KEY=value lines.
func parseShow(out string) (UnitState, error) {
	var st UnitState
	for _, line := range strings.Split(out, "\n") {
		key, value, _ := strings.Cut(strings.TrimSpace(line), "=")
		switch key {
		case "ActiveState":
			st.Active = value == "active"
		case "InvocationID":
			st.Invocation = value
		case "NRestarts":
			n, err := strconv.Atoi(value)
			if err != nil {
				return UnitState{}, fmt.Errorf("NRestarts %q: %w", value, err)
			}
			st.Restarts = n
		}
	}
	return st, nil
}

// SocketProbe asks the agent at socket for its status (root may always).
func SocketProbe(socket string) func(context.Context) (AgentStatus, error) {
	return func(ctx context.Context) (AgentStatus, error) {
		ctx, cancel := context.WithTimeout(ctx, probeTimeout)
		defer cancel()
		var st AgentStatus
		err := localrpc.Call(ctx, socket, "status", nil, &st)
		return st, err
	}
}

// ErrBusy means another update is running.
var ErrBusy = errors.New("another meteorcli update is running")

// Lock takes the update lock (a flock held until unlock or exit).
func Lock(path string) (unlock func(), err error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrBusy
		}
		return nil, err
	}
	return func() { f.Close() }, nil
}

// WriteOutcome saves o for the actions module and gives the file to the agent
// user, who removes it once the result is reported.
func WriteOutcome(stateDir string, o Outcome, owner config.Owner) error {
	st, err := state.NewStore(stateDir)
	if err != nil {
		return err
	}
	if err := st.Write(OutcomeFile, FileVersion, o); err != nil {
		return err
	}
	return owner.Agent(filepath.Join(stateDir, OutcomeFile))
}
