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
	"strings"
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
)

// DefaultMaxRequest bounds a request (a systemd unit file is the largest today).
const DefaultMaxRequest = 512 << 10

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
	finished := make(chan struct{})
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
			inFlight++
			idle = nil
			go func() { // owned: Serve waits for it below
				defer func() { finished <- struct{}{} }()
				s.handle(ctx, c)
			}()
		case <-finished:
			inFlight--
			resetIdle()
		}
	}
	l.Close()
	cancel()
	for ; inFlight > 0; inFlight-- {
		<-finished
	}
	<-done
	return result
}

func (s *Server) handle(ctx context.Context, c *net.UnixConn) {
	defer c.Close()
	start := time.Now()
	_ = c.SetDeadline(start.Add(s.timeout() + 10*time.Second))

	peer, err := PeerOf(c)
	if err != nil {
		s.Log.Warn("rejected connection without peer credentials", "err", err)
		return
	}
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
	if len(req.Params) > 0 {
		attrs = append(attrs, "params", truncate(string(req.Params), 300))
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

// Call sends one request to the socket at path and decodes the result into out
// (when not nil). A missing socket or one nobody listens on returns CodeUnavailable;
// a socket the caller may not open returns CodeDenied.
func Call(ctx context.Context, path, method string, params, out any) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout(ctx))
	defer cancel()
	var d net.Dialer
	conn, err := d.DialContext(ctx, "unix", path)
	if err != nil {
		switch {
		case errors.Is(err, syscall.EACCES), errors.Is(err, syscall.EPERM):
			return Errorf(CodeDenied, "cannot open %s: permission denied", path)
		case errors.Is(err, syscall.ENOENT), errors.Is(err, syscall.ECONNREFUSED):
			return Errorf(CodeUnavailable, "nothing listens on %s", path)
		}
		return Errorf(CodeUnavailable, "%v", err)
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	}
	req := Request{Method: method}
	if params != nil {
		if req.Params, err = json.Marshal(params); err != nil {
			return fmt.Errorf("%s: %w", method, err)
		}
	}
	if err := json.NewEncoder(conn).Encode(req); err != nil {
		return fmt.Errorf("%s: send: %w", method, err)
	}
	var resp Response
	if err := json.NewDecoder(io.LimitReader(conn, 16<<20)).Decode(&resp); err != nil {
		return fmt.Errorf("%s: no answer: %w", method, err)
	}
	if !resp.OK {
		if resp.Error == nil {
			return Errorf(CodeFailed, "%s failed", method)
		}
		return resp.Error
	}
	if out != nil && len(resp.Result) > 0 {
		if err := json.Unmarshal(resp.Result, out); err != nil {
			return fmt.Errorf("%s: decode answer: %w", method, err)
		}
	}
	return nil
}

// callTimeout keeps an earlier deadline from ctx, else allows 60 s.
func callTimeout(ctx context.Context) time.Duration {
	if d, ok := ctx.Deadline(); ok {
		return time.Until(d)
	}
	return 60 * time.Second
}

func truncate(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
