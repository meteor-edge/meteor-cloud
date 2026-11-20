// Package helper is the small root process that does the few privileged things
// the agent needs (reboot, read the journal, install a systemd unit, ...). It is
// socket-activated by meteorcli-helper.socket, answers only the meteor user, and
// runs only the operations listed in ops.go, each with validated arguments, a
// timeout and an audit line in the journal. The agent itself has no privileges.
package helper

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"strconv"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
)

// DefaultIdle is how long the helper waits for the next request before it exits;
// systemd starts it again on the next connection.
const DefaultIdle = time.Minute

// Server answers helper requests.
type Server struct {
	// AllowedUID is the only caller served (the meteor user).
	AllowedUID uint32
	// Host is what the operations act on.
	Host Host
	// Log receives the audit lines; it must redact secrets.
	Log *slog.Logger
	// Idle ends Serve after this long without requests; zero means never.
	Idle time.Duration
}

// Serve answers requests on l until ctx ends or the server was idle for Idle.
func (s *Server) Serve(ctx context.Context, l *net.UnixListener) error {
	rpc := &localrpc.Server{
		Handlers:  Ops(s.Host),
		Authorize: s.authorize,
		Log:       s.Log,
		Idle:      s.Idle,
		Timeout:   2 * time.Minute,
	}
	return rpc.Serve(ctx, l)
}

func (s *Server) authorize(p localrpc.Peer, _ string) error {
	if p.UID != s.AllowedUID {
		return localrpc.Errorf(localrpc.CodeDenied, "the helper serves only uid %d, not uid %d", s.AllowedUID, p.UID)
	}
	return nil
}

// ListenerFromSystemd returns the socket systemd passed (LISTEN_FDS, fd 3).
func ListenerFromSystemd() (*net.UnixListener, error) {
	if os.Getenv("LISTEN_PID") != strconv.Itoa(os.Getpid()) {
		return nil, errors.New("helper: not started by systemd socket activation (LISTEN_PID)")
	}
	n, err := strconv.Atoi(os.Getenv("LISTEN_FDS"))
	if err != nil || n < 1 {
		return nil, errors.New("helper: systemd passed no socket (LISTEN_FDS)")
	}
	f := os.NewFile(3, "meteorcli-helper.socket")
	defer f.Close()
	l, err := net.FileListener(f)
	if err != nil {
		return nil, fmt.Errorf("helper: %w", err)
	}
	ul, ok := l.(*net.UnixListener)
	if !ok {
		l.Close()
		return nil, errors.New("helper: the passed socket is not a Unix socket")
	}
	return ul, nil
}

// Client is how the agent asks the helper for privileged work.
type Client struct {
	path string
}

// NewClient returns a client for the helper socket at path.
func NewClient(path string) *Client { return &Client{path: path} }

// Call runs one operation and decodes its result into out (may be nil).
func (c *Client) Call(ctx context.Context, op string, params, out any) error {
	if err := localrpc.Call(ctx, c.path, op, params, out); err != nil {
		return fmt.Errorf("helper %s: %w", op, err)
	}
	return nil
}
