package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// Groups that may use the agent socket. Root may always.
const (
	// GroupRead may run status, logs, test, doctor, mqtt-listen.
	GroupRead = "meteorcli"
	// GroupAdmin may also enroll, change config, update and restart modules.
	GroupAdmin = "meteorcli-admin"
)

// access is who may call a socket method.
type access int

const (
	readAccess access = iota
	adminAccess
)

// socketPolicy lists every method the agent socket serves and who may call it.
// Add a method: its handler in handlers() and one line here.
var socketPolicy = map[string]access{
	"status":         readAccess,
	"module.restart": adminAccess,
}

// StatusReply answers `meteorcli status`. It says whether secrets exist, never
// what they are.
type StatusReply struct {
	Version          string                      `json:"version"`
	State            string                      `json:"state"`
	Domain           string                      `json:"domain"`
	Server           string                      `json:"server"`
	APIKey           bool                        `json:"api_key_present"`
	Credential       bool                        `json:"credential_present"`
	MQTT             bool                        `json:"mqtt_configured"`
	WireGuard        bool                        `json:"wireguard_key_present"`
	PendingClaim     bool                        `json:"pending_claim"`
	DeviceID         string                      `json:"device_id"`
	OrganizationID   string                      `json:"organization_id"`
	Name             string                      `json:"name"`
	HeartbeatSeconds int                         `json:"heartbeat_seconds"`
	Modules          map[string]api.ModuleHealth `json:"modules"`
}

// Agent states shown by status.
const (
	StateNotConfigured        = "not_configured"
	StateWaitingForEnrollment = "waiting_for_enrollment"
	StateEnrolled             = "enrolled"
)

// ModuleRestartParams is the body of module.restart.
type ModuleRestartParams struct {
	Name string `json:"name"`
}

// SocketModule serves /run/meteorcli/agent.sock for the CLI. It is critical: the
// device must stay manageable even when every feature module is broken.
type SocketModule struct {
	sup    *Supervisor
	groups localrpc.Groups
	// tick is how often the module reports progress to the health registry.
	tick time.Duration
}

// NewSocketModule returns the socket module for sup, which handles module.restart.
func NewSocketModule(sup *Supervisor, groups localrpc.Groups) *SocketModule {
	return &SocketModule{sup: sup, groups: groups, tick: 30 * time.Second}
}

// Name implements Module.
func (m *SocketModule) Name() string { return "socket" }

// Critical implements Module.
func (m *SocketModule) Critical() bool { return true }

// Run implements Module: listen, serve until ctx ends.
func (m *SocketModule) Run(ctx context.Context, d Deps) error {
	l, release, err := listenAgentSocket(d.Paths.AgentSocket())
	if err != nil {
		return err
	}
	// Released after Serve returned and closed (and unlinked) the socket.
	defer release()
	srv := &localrpc.Server{
		Handlers:  m.handlers(d),
		Authorize: m.authorize,
		Log:       d.Log,
	}
	served := make(chan error, 1)
	go func() { served <- srv.Serve(ctx, l) }() // owned: Run waits for it
	t := time.NewTicker(m.tick)
	defer t.Stop()
	d.Health.Tick(m.Name())
	for {
		select {
		case err := <-served:
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("socket: %w", err)
		case <-t.C:
			d.Health.Tick(m.Name())
		}
	}
}

// authorize applies socketPolicy to the caller the kernel reported.
func (m *SocketModule) authorize(p localrpc.Peer, method string) error {
	need, ok := socketPolicy[method]
	if !ok {
		return localrpc.Errorf(localrpc.CodeUnknownMethod, "unknown method %q", method)
	}
	if p.UID == 0 || m.groups.Member(p.UID, p.GID, GroupAdmin) {
		return nil
	}
	if need == readAccess && m.groups.Member(p.UID, p.GID, GroupRead) {
		return nil
	}
	group := GroupRead
	if need == adminAccess {
		group = GroupAdmin
	}
	return localrpc.Errorf(localrpc.CodeDenied, "%s needs the %s group (or root)", method, group)
}

func (m *SocketModule) handlers(d Deps) map[string]localrpc.Handler {
	return map[string]localrpc.Handler{
		"status": {Run: func(context.Context, localrpc.Peer, json.RawMessage) (any, error) {
			return status(d), nil
		}},
		"module.restart": {Audit: func(raw json.RawMessage) []any {
			var p ModuleRestartParams
			_ = json.Unmarshal(raw, &p)
			return []any{"name", p.Name}
		}, Run: func(_ context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
			var p ModuleRestartParams
			if err := json.Unmarshal(raw, &p); err != nil || p.Name == "" {
				return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params must be {\"name\": \"<module>\"}")
			}
			if err := m.sup.Restart(p.Name); err != nil {
				if errors.Is(err, ErrUnknownModule) {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "no module named %q", p.Name)
				}
				return nil, err
			}
			return map[string]string{"restarted": p.Name}, nil
		}},
	}
}

// status collects what `meteorcli status` shows, as the agent user sees it.
func status(d Deps) StatusReply {
	p := d.Paths
	r := StatusReply{
		Version:      d.Version,
		State:        StateNotConfigured,
		APIKey:       exists(p.APIKeyFile()),
		Credential:   exists(p.DeviceTokenFile()),
		MQTT:         exists(p.MQTTFile()),
		WireGuard:    exists(filepath.Join(p.WireGuardDir(), "public.key")),
		PendingClaim: exists(p.PendingClaimFile()),
		Modules:      d.Health.Snapshot(),
	}
	if d.Config != nil {
		r.Domain, r.Server = d.Config.Domain, d.Config.Server()
	}
	dev, err := config.LoadDevice(p.DeviceFile())
	if err == nil {
		r.DeviceID, r.OrganizationID, r.Name = dev.DeviceID, dev.OrganizationID, dev.Name
	}
	r.HeartbeatSeconds = int(dev.Heartbeat().Seconds())
	switch {
	case r.Credential && r.DeviceID != "":
		r.State = StateEnrolled
	case r.Server != "":
		r.State = StateWaitingForEnrollment
	}
	return r
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// listenAgentSocket takes the socket's lock, replaces a stale socket, and makes
// the new one meteor:meteorcli 0660, so only the groups can connect. The lock
// (an flock on <path>.lock, freed by the kernel if the agent dies) is held until
// release is called, after the listener closed: two agents with the same paths
// can never remove each other's live socket.
func listenAgentSocket(path string) (l *net.UnixListener, release func(), err error) {
	unlock, err := lockSocket(path)
	if err != nil {
		return nil, nil, err
	}
	defer func() {
		if err != nil {
			unlock()
		}
	}()
	// We hold the lock, so whatever socket is there has no owner any more.
	if err := removeStaleSocket(path); err != nil {
		return nil, nil, err
	}
	l, err = net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return nil, nil, fmt.Errorf("socket: %w", err)
	}
	l.SetUnlinkOnClose(true)
	if err := os.Chmod(path, 0o660); err != nil {
		l.Close()
		return nil, nil, fmt.Errorf("socket: chmod %s: %w", path, err)
	}
	if gid, ok := localrpc.LookupGID(GroupRead); ok {
		// The agent may hand the socket to a group it belongs to
		// (SupplementaryGroups=meteorcli in the unit); no capability needed.
		if err := os.Chown(path, -1, gid); err != nil {
			l.Close()
			return nil, nil, fmt.Errorf("socket: chgrp %s %s: %w", GroupRead, path, err)
		}
	}
	return l, unlock, nil
}

// lockSocket takes an exclusive, non-blocking flock on path+".lock".
func lockSocket(path string) (unlock func(), err error) {
	f, err := os.OpenFile(path+".lock", os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, fmt.Errorf("socket: lock: %w", err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, fmt.Errorf("socket: another agent owns %s", path)
		}
		return nil, fmt.Errorf("socket: lock %s: %w", path, err)
	}
	return func() { f.Close() }, nil // closing the file drops the lock
}

// removeStaleSocket removes a socket left by an agent that died. The caller
// holds the socket's lock. Anything that is not a socket is left alone.
func removeStaleSocket(path string) error {
	st, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("socket: %w", err)
	}
	if st.Mode()&fs.ModeSocket == 0 {
		return fmt.Errorf("socket: %s exists and is not a socket", path)
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("socket: remove stale %s: %w", path, err)
	}
	return nil
}
