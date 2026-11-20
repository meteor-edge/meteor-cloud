// Package localrpc is the request/response protocol on meteorcli's Unix sockets
// (/run/meteorcli/agent.sock and helper.sock): one JSON request per connection,
// one JSON answer, then close. The server learns who is calling from the kernel
// (SO_PEERCRED), never from the request, and lets an Authorize hook decide.
package localrpc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"syscall"
	"time"
)

// Error codes a caller can branch on.
const (
	CodeDenied        = "permission_denied"
	CodeUnknownMethod = "unknown_method"
	CodeBadRequest    = "bad_request"
	CodeFailed        = "failed"
	CodeUnavailable   = "unavailable" // the socket is missing or nobody listens
	CodeBusy          = "busy"        // the server closed the connection unanswered (at its limit)
)

// Limits that keep a local caller from exhausting the server's memory.
const (
	// DefaultMaxRequest bounds a request (a systemd unit file is the largest today).
	DefaultMaxRequest = 512 << 10
	// DefaultMaxConns bounds connections served at once; more are closed at once.
	DefaultMaxConns = 16
	// DefaultMaxPerUser bounds connections one uid holds, so one user cannot
	// take every slot and lock the others out.
	DefaultMaxPerUser = 4
	// rootReserve is extra room only root may use when the server is full.
	rootReserve = 4
	// readTimeout bounds sending the request, so a slow client frees its slot.
	readTimeout = 5 * time.Second
)

// Request is what a client sends.
type Request struct {
	Method string          `json:"method"`
	Params json.RawMessage `json:"params,omitempty"`
}

// Response is what the server answers.
type Response struct {
	OK     bool            `json:"ok"`
	Error  *Error          `json:"error,omitempty"`
	Result json.RawMessage `json:"result,omitempty"`
}

// Error is a failed call. Handlers return it to choose the code; any other
// error becomes CodeFailed.
type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	// RetryAfter is how many seconds the caller should wait before trying
	// again, when the work failed because a server asked to slow down.
	RetryAfter int `json:"retry_after,omitempty"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// Errorf builds an *Error.
func Errorf(code, format string, args ...any) *Error {
	return &Error{Code: code, Message: fmt.Sprintf(format, args...)}
}

// CodeOf returns the code of err, or "" when it is not an *Error.
func CodeOf(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

// Peer is the calling process as the kernel reports it.
type Peer struct {
	UID, GID uint32
	PID      int32
}

// Handler runs one method. Params are the raw request params.
type Handler struct {
	Run func(ctx context.Context, p Peer, params json.RawMessage) (any, error)
	// Timeout bounds Run; zero means Server.Timeout.
	Timeout time.Duration
	// Audit returns the few params worth logging (a unit name, a module name).
	// The audit line never carries raw params: they can hold secrets.
	Audit func(params json.RawMessage) []any
}

// Server answers requests on a Unix listener.
type Server struct {
	// Handlers maps a method name to its handler.
	Handlers map[string]Handler
	// Authorize returns nil when p may call method. Required.
	Authorize func(p Peer, method string) error
	// Log receives one audit line per request (the caller's uid and pid,
	// method, outcome). It should redact secrets.
	Log *slog.Logger
	// Timeout bounds a handler without its own; zero means 30 s.
	Timeout time.Duration
	// MaxRequest bounds a request; zero means DefaultMaxRequest.
	MaxRequest int64
	// MaxConns bounds connections served at once; zero means DefaultMaxConns.
	MaxConns int
	// MaxPerUser bounds connections from one uid; zero means DefaultMaxPerUser.
	MaxPerUser int
	// Idle, when set, makes Serve return after this long without a connection
	// (socket-activated services exit and systemd starts them again on demand).
	Idle time.Duration
}

// Serve accepts connections until ctx ends (or Idle passes) and waits for the
// requests in flight before it returns. Each connection runs in a goroutine that
// Serve owns.
func (s *Server) Serve(ctx context.Context, l *net.UnixListener) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan struct{})
	conns := make(chan *net.UnixConn)
	type ended struct {
		uid  uint32
		conn *net.UnixConn
	}
	acceptErr := make(chan error, 1)
	go func() { // owned: ends when the listener closes below
		defer close(done)
		for {
			c, err := l.AcceptUnix()
			if err != nil {
				acceptErr <- err
				return
			}
			select {
			case conns <- c:
			case <-ctx.Done():
				c.Close()
				return
			}
		}
	}()

	var inFlight int
	perUser := map[uint32]int{}
	open := map[*net.UnixConn]bool{}
	finished := make(chan ended)
	var idle <-chan time.Time
	resetIdle := func() {
		if s.Idle > 0 && inFlight == 0 {
			idle = time.After(s.Idle)
		}
	}
	resetIdle()
	var result error
loop:
	for {
		select {
		case <-ctx.Done():
			break loop
		case <-idle:
			break loop
		case err := <-acceptErr:
			result = fmt.Errorf("accept: %w", err)
			break loop
		case c := <-conns:
			peer, err := PeerOf(c)
			if err != nil {
				s.Log.Warn("rejected connection without peer credentials", "err", err)
				c.Close()
				continue
			}
			if why := s.admit(peer, inFlight, perUser[peer.UID]); why != "" {
				s.Log.Warn("connection refused: "+why, "uid", peer.UID, "pid", peer.PID)
				c.Close()
				continue
			}
			// Deadlines are set here, not in the handler, so the shutdown below
			// (same goroutine) always comes after them and is never overwritten.
			now := time.Now()
			_ = c.SetReadDeadline(now.Add(readTimeout))
			_ = c.SetWriteDeadline(now.Add(readTimeout + s.longestTimeout() + 10*time.Second))
			inFlight++
			perUser[peer.UID]++
			open[c] = true
			idle = nil
			go func() { // owned: Serve waits for it below
				defer func() { finished <- ended{peer.UID, c} }()
				s.handle(ctx, c, peer)
			}()
		case e := <-finished:
			inFlight--
			delete(open, e.conn)
			if perUser[e.uid]--; perUser[e.uid] <= 0 {
				delete(perUser, e.uid)
			}
			resetIdle()
		}
	}
	l.Close()
	cancel()
	// A client that has not sent its request yet does not hold up shutdown.
	for c := range open {
		_ = c.SetReadDeadline(time.Now())
	}
	for ; inFlight > 0; inFlight-- {
		<-finished
	}
	<-done
	return result
}

// admit says why a new connection from p is refused, or "" to serve it. Each uid
// gets at most MaxPerUser slots, and root has a few slots beyond MaxConns, so a
// user holding connections open can lock out neither other users nor root.
func (s *Server) admit(p Peer, inFlight, fromPeer int) string {
	limit := s.maxConns()
	if p.UID == 0 {
		limit += rootReserve
	} else if fromPeer >= s.maxPerUser() {
		return "too many connections from this user"
	}
	if inFlight >= limit {
		return "too many connections"
	}
	return ""
}

func (s *Server) handle(ctx context.Context, c *net.UnixConn, peer Peer) {
	defer c.Close()
	start := time.Now()

	req, err := s.read(c)
	if err != nil {
		s.Log.Warn("bad request", "uid", peer.UID, "pid", peer.PID, "err", err)
		write(c, nil, Errorf(CodeBadRequest, "%v", err))
		return
	}
	result, err := s.call(ctx, peer, req)
	write(c, result, err)

	outcome := "ok"
	if err != nil {
		outcome = CodeOf(err)
		if outcome == "" {
			outcome = CodeFailed
		}
	}
	attrs := []any{"method", req.Method, "uid", peer.UID, "pid", peer.PID, "outcome", outcome,
		"duration", time.Since(start).Round(time.Millisecond)}
	if h, ok := s.Handlers[req.Method]; ok && h.Audit != nil {
		attrs = append(attrs, h.Audit(req.Params)...)
	}
	if err != nil {
		attrs = append(attrs, "err", err)
		s.Log.Warn("request", attrs...)
		return
	}
	s.Log.Info("request", attrs...)
}

func (s *Server) call(ctx context.Context, peer Peer, req Request) (any, error) {
	if err := s.Authorize(peer, req.Method); err != nil {
		if CodeOf(err) == "" {
			err = Errorf(CodeDenied, "%v", err)
		}
		return nil, err
	}
	h, ok := s.Handlers[req.Method]
	if !ok {
		return nil, Errorf(CodeUnknownMethod, "unknown method %q", req.Method)
	}
	timeout := h.Timeout
	if timeout <= 0 {
		timeout = s.timeout()
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return h.Run(ctx, peer, req.Params)
}

func (s *Server) read(c *net.UnixConn) (Request, error) {
	limit := s.MaxRequest
	if limit <= 0 {
		limit = DefaultMaxRequest
	}
	var req Request
	dec := json.NewDecoder(io.LimitReader(c, limit))
	if err := dec.Decode(&req); err != nil {
		return req, fmt.Errorf("request is not JSON or larger than %d bytes", limit)
	}
	if req.Method == "" {
		return req, errors.New("request has no method")
	}
	return req, nil
}

func (s *Server) maxConns() int {
	if s.MaxConns > 0 {
		return s.MaxConns
	}
	return DefaultMaxConns
}

func (s *Server) maxPerUser() int {
	if s.MaxPerUser > 0 {
		return s.MaxPerUser
	}
	return DefaultMaxPerUser
}

// longestTimeout is the longest any handler may run. The connection's write
// deadline is set before the method is known, so it must allow the longest.
func (s *Server) longestTimeout() time.Duration {
	longest := s.timeout()
	for _, h := range s.Handlers {
		longest = max(longest, h.Timeout)
	}
	return longest
}

func (s *Server) timeout() time.Duration {
	if s.Timeout > 0 {
		return s.Timeout
	}
	return 30 * time.Second
}

func write(c *net.UnixConn, result any, err error) {
	resp := Response{OK: err == nil}
	if err != nil {
		var e *Error
		if !errors.As(err, &e) {
			e = Errorf(CodeFailed, "%v", err)
		}
		resp.Error = e
	} else if result != nil {
		data, merr := json.Marshal(result)
		if merr != nil {
			resp = Response{Error: Errorf(CodeFailed, "encode result: %v", merr)}
		} else {
			resp.Result = data
		}
	}
	_ = json.NewEncoder(c).Encode(resp)
}

// PeerOf asks the kernel who is on the other end of c.
func PeerOf(c *net.UnixConn) (Peer, error) {
	raw, err := c.SyscallConn()
	if err != nil {
		return Peer{}, err
	}
	var cred *syscall.Ucred
	var credErr error
	if err := raw.Control(func(fd uintptr) {
		cred, credErr = syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	}); err != nil {
		return Peer{}, err
	}
	if credErr != nil {
		return Peer{}, fmt.Errorf("SO_PEERCRED: %w", credErr)
	}
	return Peer{UID: cred.Uid, GID: cred.Gid, PID: cred.Pid}, nil
}
