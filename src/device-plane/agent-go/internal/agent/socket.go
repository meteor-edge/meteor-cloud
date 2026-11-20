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
	"meteorcloud/meteorcli/internal/identity"
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
	"test":           readAccess,
	"module.restart": adminAccess,
	"register":       adminAccess,
	"enroll.request": adminAccess,
	"enroll.claim":   adminAccess,
	"config.reload":  adminAccess,
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
	PendingRequestID string                      `json:"pending_request_id"`
	DeviceID         string                      `json:"device_id"`
	OrganizationID   string                      `json:"organization_id"`
	Name             string                      `json:"name"`
	HeartbeatSeconds int                         `json:"heartbeat_seconds"`
	Modules          map[string]api.ModuleHealth `json:"modules"`
}

// Agent states shown by status (the identity's state).
const (
	StateNotConfigured        = identity.StateNotConfigured
	StateWaitingForEnrollment = identity.StateWaiting
	StatePendingApproval      = identity.StatePending
	StateEnrolled             = identity.StateEnrolled
	StateCredentialRejected   = identity.StateRejected
)

// ModuleRestartParams is the body of module.restart.
type ModuleRestartParams struct {
	Name string `json:"name"`
}

// SocketModule serves /run/meteorcli/agent.sock for the CLI. It is critical: the
// device must stay manageable even when every feature module is broken.
type SocketModule struct {
	sup      *Supervisor
	groups   localrpc.Groups
	identity *identity.Service
	// OwnUID is the agent's own uid (the meteor user). That user owns every
	// file of the agent already, so it may call every method; this also lets
	// tests and development run the agent and the CLI as one user. Tests of
	// the group policy set another uid.
	OwnUID uint32
	// tick is how often the module reports progress to the health registry.
	tick time.Duration
}

// NewSocketModule returns the socket module. sup handles module.restart; id
// performs register, request-token, claim, test and config reload (nil in
// tests that do not need them).
func NewSocketModule(sup *Supervisor, groups localrpc.Groups, id *identity.Service) *SocketModule {
	return &SocketModule{sup: sup, groups: groups, identity: id, OwnUID: uint32(os.Getuid()), tick: 30 * time.Second}
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
	if p.UID == 0 || p.UID == m.OwnUID || m.groups.Member(p.UID, p.GID, GroupAdmin) {
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
	h := map[string]localrpc.Handler{
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
	for name, handler := range m.enrollHandlers() {
		h[name] = handler
	}
	return h
}

// status collects what `meteorcli status` shows from the live identity.
func status(d Deps) StatusReply {
	st := d.Identity.Status()
	return StatusReply{
		Version:          d.Version,
		State:            st.State,
		Domain:           st.Domain,
		Server:           st.Server,
		APIKey:           st.APIKey,
		Credential:       st.Credential,
		MQTT:             st.MQTT,
		WireGuard:        exists(filepath.Join(d.Paths.WireGuardDir(), "public.key")),
		PendingClaim:     st.PendingRequestID != "",
		PendingRequestID: st.PendingRequestID,
		DeviceID:         st.DeviceID,
		OrganizationID:   st.OrganizationID,
		Name:             st.Name,
		HeartbeatSeconds: st.HeartbeatSeconds,
		Modules:          d.Health.Snapshot(),
	}
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
// holds the socket's lock; as a second guard (against a process that does not
// take the lock) a socket is removed only when connecting is refused outright.
// A socket that answers, any other dial error, and anything that is not a
// socket are left alone.
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
	c, err := net.DialTimeout("unix", path, time.Second)
	if err == nil {
		c.Close()
		return fmt.Errorf("socket: another process serves %s; stop it first", path)
	}
	if !errors.Is(err, syscall.ECONNREFUSED) {
		return fmt.Errorf("socket: cannot tell whether %s is stale: %w", path, err)
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("socket: remove stale %s: %w", path, err)
	}
	return nil
}
