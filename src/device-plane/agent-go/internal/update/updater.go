// Package update is `meteorcli updater`: the root process the helper starts
// (update.start) as its own transient systemd unit, so stopping the agent
// cannot stop it. It downloads the release manifest and checks its ed25519
// signature against the keys compiled into this binary, downloads the binary
// for this CPU and checks its SHA-256 and size, keeps the installed binary as
// meteorcli.previous, swaps the new one in, restarts the agent and waits for a
// heartbeat from the new version. If the agent does not come up, the previous
// binary is put back and started again. The result goes to an outcome file
// the actions module reports as the update_agent result.
package update

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"time"
)

// Units the updater acts on.
const (
	AgentUnit  = "meteorcli.service"
	HelperUnit = "meteorcli-helper.service"
)

// Defaults of one update.
const (
	DefaultHealthTimeout = 3 * time.Minute
	defaultPoll          = 2 * time.Second
	// defaultSettle is how long a device that is not enrolled (no heartbeat
	// possible) must keep answering on its socket to count as healthy.
	defaultSettle = 10 * time.Second
	maxManifest   = 1 << 20
	maxSignature  = 4 << 10
	// spareBytes is the free space kept beyond the new binary.
	spareBytes = 16 << 20
)

// System is what the updater does with systemd. The real one runs systemctl.
type System interface {
	Restart(ctx context.Context, unit string) error
	Stop(ctx context.Context, unit string) error
	// Active reports whether the unit is active (not activating, failed, ...).
	Active(ctx context.Context, unit string) (bool, error)
	// Restarts is the unit's NRestarts: automatic restarts after a crash.
	Restarts(ctx context.Context, unit string) (int, error)
}

// AgentStatus is the part of the agent's status the health check reads.
type AgentStatus struct {
	Version       string     `json:"version"`
	State         string     `json:"state"`
	LastHeartbeat *time.Time `json:"last_heartbeat"`
}

// enrolledState is identity.StateEnrolled (the agent socket's status state).
const enrolledState = "enrolled"

// Updater replaces the installed meteorcli with a newer signed release.
type Updater struct {
	// Fetch downloads release files by name.
	Fetch Fetcher
	// Keys verify the manifest signature.
	Keys []ed25519.PublicKey
	// Asset is the binary for this CPU (AssetName).
	Asset string
	// Bin is the installed binary (/usr/local/bin/meteorcli).
	Bin string
	// Current is the installed version (this process's own).
	Current string
	// System restarts and watches units.
	System System
	// Probe asks the running agent for its status (agent.sock).
	Probe func(ctx context.Context) (AgentStatus, error)
	// Smoke runs a binary with --version and returns what it printed.
	Smoke func(ctx context.Context, path string) (string, error)
	// FreeBytes reports the free space in a directory; nil skips the check.
	FreeBytes func(dir string) (uint64, error)
	// HealthTimeout bounds the wait for the new agent's first heartbeat.
	HealthTimeout time.Duration
	// Poll and Settle tune the health check (tests shorten them).
	Poll, Settle time.Duration
	Log          *slog.Logger
}

// Previous is where the replaced binary is kept.
func (u *Updater) Previous() string { return u.Bin + ".previous" }

func (u *Updater) newPath() string {
	return filepath.Join(filepath.Dir(u.Bin), "."+filepath.Base(u.Bin)+".new")
}

// Run performs one update and says what happened. It never returns with the
// agent stopped: every failure after the swap restores the previous binary.
func (u *Updater) Run(ctx context.Context) Outcome {
	out := Outcome{FromVersion: u.Current}
	finish := func(status string, err error) Outcome {
		out.Status, out.FinishedAt = status, time.Now().UTC()
		if err != nil {
			out.Error = err.Error()
		}
		return out
	}
	m, err := u.manifest(ctx)
	if err != nil {
		return finish(StatusFailed, err)
	}
	out.ToVersion = m.Version
	if Same(m.Version, u.Current) {
		u.Log.Info("already up to date", "version", u.Current)
		return finish(StatusAlreadyCurrent, nil)
	}
	if !Newer(m.Version, u.Current) {
		// Like the Python updater: a published release older than the
		// installed build is not an update, and not an error either.
		u.Log.Info("installed build is newer than the release", "installed", u.Current, "release", m.Version)
		out.ToVersion = u.Current
		return finish(StatusAlreadyCurrent, nil)
	}
	file, err := m.Binary(u.Asset)
	if err != nil {
		return finish(StatusFailed, err)
	}
	u.Log.Info("updating", "from", u.Current, "to", m.Version, "asset", u.Asset)
	if err := u.stage(ctx, m.Version, file); err != nil {
		_ = os.Remove(u.newPath())
		return finish(StatusFailed, err)
	}
	if err := u.swap(); err != nil {
		_ = os.Remove(u.newPath())
		return finish(StatusFailed, fmt.Errorf("install the new binary: %w", err))
	}
	if err := u.startAndWait(ctx, m.Version); err != nil {
		u.Log.Error("new version is not healthy; rolling back", "version", m.Version, "err", err)
		if rerr := u.rollback(ctx); rerr != nil {
			return finish(StatusFailed, fmt.Errorf("%v; rollback failed: %v", err, rerr))
		}
		return finish(StatusRolledBack, fmt.Errorf("%v; rolled back to %s", err, u.Current))
	}
	u.Log.Info("updated", "from", u.Current, "to", m.Version, "previous", u.Previous())
	return finish(StatusUpdated, nil)
}

// manifest downloads and verifies the signed manifest.
func (u *Updater) manifest(ctx context.Context) (*Manifest, error) {
	raw, err := u.Fetch.Get(ctx, ManifestName, maxManifest)
	if err != nil {
		return nil, err
	}
	sig, err := u.Fetch.Get(ctx, SignatureName, maxSignature)
	if err != nil {
		return nil, fmt.Errorf("%w (%v)", ErrUnsigned, err)
	}
	return VerifyManifest(raw, sig, u.Keys)
}

// stage downloads the binary next to Bin (same filesystem, so the swap is a
// rename; /tmp may be noexec), checks it and runs it once.
func (u *Updater) stage(ctx context.Context, version string, f File) error {
	dir := filepath.Dir(u.Bin)
	if u.FreeBytes != nil {
		free, err := u.FreeBytes(dir)
		if err == nil && free < uint64(f.Size)+spareBytes {
			return fmt.Errorf("not enough free space in %s: %d MB free, need %d MB",
				dir, free>>20, (uint64(f.Size)+spareBytes)>>20)
		}
	}
	tmp := u.newPath()
	if err := u.Fetch.Download(ctx, u.Asset, f, tmp); err != nil {
		return err
	}
	got, err := u.Smoke(ctx, tmp)
	if err != nil {
		return fmt.Errorf("the new binary does not run here: %w", err)
	}
	if !Same(got, version) {
		return fmt.Errorf("the new binary reports %q, the release says %s", got, version)
	}
	return nil
}

// swap keeps the installed binary as .previous (a hard link: no copy, same
// inode) and renames the new one over Bin, so Bin is never missing.
func (u *Updater) swap() error {
	prev := u.Previous()
	if err := os.Remove(prev); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := linkOrCopy(u.Bin, prev); err != nil {
		return fmt.Errorf("keep %s: %w", prev, err)
	}
	if err := os.Rename(u.newPath(), u.Bin); err != nil {
		return err
	}
	return syncDir(filepath.Dir(u.Bin))
}

// startAndWait restarts the agent on the new binary, lets the helper start on
// it at the next request, and waits for the health check.
func (u *Updater) startAndWait(ctx context.Context, version string) error {
	if err := u.System.Restart(ctx, AgentUnit); err != nil {
		return fmt.Errorf("meteorcli %s did not start: %w", version, err)
	}
	u.stopHelper(ctx)
	return u.waitHealthy(ctx, version)
}

// stopHelper stops the running helper; its socket starts the new binary on the
// next request. A failure only delays that until the helper is idle.
func (u *Updater) stopHelper(ctx context.Context) {
	if err := u.System.Stop(ctx, HelperUnit); err != nil {
		u.Log.Warn("could not stop the helper; it switches when idle", "err", err)
	}
}

// waitHealthy waits until the agent runs version without crashing and, when
// enrolled, has sent a heartbeat the server accepted. A device that is not
// enrolled cannot send one: it must keep answering on its socket for Settle.
func (u *Updater) waitHealthy(ctx context.Context, version string) error {
	timeout, poll, settle := u.HealthTimeout, u.Poll, u.Settle
	if timeout <= 0 {
		timeout = DefaultHealthTimeout
	}
	if poll <= 0 {
		poll = defaultPoll
	}
	if settle <= 0 {
		settle = defaultSettle
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	base, err := u.System.Restarts(ctx, AgentUnit)
	if err != nil {
		return fmt.Errorf("read meteorcli.service restarts: %w", err)
	}
	var answered time.Time
	why := "it did not answer on its socket"
	t := time.NewTicker(poll)
	defer t.Stop()
	for {
		if n, err := u.System.Restarts(ctx, AgentUnit); err == nil && n > base {
			return fmt.Errorf("meteorcli %s crashed and was restarted by systemd", version)
		}
		if active, err := u.System.Active(ctx, AgentUnit); err == nil && !active {
			return fmt.Errorf("meteorcli %s stopped running", version)
		}
		st, err := u.Probe(ctx)
		switch {
		case err != nil:
		case !Same(st.Version, version):
			why = fmt.Sprintf("the agent reports version %s", st.Version)
		case st.State == enrolledState:
			if st.LastHeartbeat != nil {
				u.Log.Info("new version sent a heartbeat", "version", version, "at", st.LastHeartbeat.UTC())
				return nil
			}
			why = "it sent no heartbeat the server accepted"
		default:
			if answered.IsZero() {
				answered = time.Now()
			}
			if time.Since(answered) >= settle {
				u.Log.Info("new version is running (not enrolled: no heartbeat to wait for)", "version", version, "state", st.State)
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("meteorcli %s was not healthy within %s: %s", version, timeout, why)
		case <-t.C:
		}
	}
}

// rollback puts the previous binary back (keeping .previous) and restarts the
// agent and the helper on it.
func (u *Updater) rollback(ctx context.Context) error {
	prev := u.Previous()
	tmp := u.newPath()
	_ = os.Remove(tmp)
	if err := linkOrCopy(prev, tmp); err != nil {
		return fmt.Errorf("restore %s: %w", prev, err)
	}
	if err := os.Rename(tmp, u.Bin); err != nil {
		return fmt.Errorf("restore %s: %w", prev, err)
	}
	_ = syncDir(filepath.Dir(u.Bin))
	// A fresh context: the health wait may have used up the caller's.
	rctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Minute)
	defer cancel()
	if err := u.System.Restart(rctx, AgentUnit); err != nil {
		return fmt.Errorf("restart meteorcli %s: %w", u.Current, err)
	}
	u.stopHelper(rctx)
	u.Log.Info("rolled back", "version", u.Current)
	return nil
}
