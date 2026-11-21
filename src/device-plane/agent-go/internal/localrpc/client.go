package localrpc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"syscall"
	"time"
)

// Call sends one request to the socket at path and decodes the result into out
// (when not nil). A missing socket or one nobody listens on returns CodeUnavailable;
// a socket the caller may not open returns CodeDenied.
func Call(ctx context.Context, path, method string, params, out any) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout(ctx))
	defer cancel()
	conn, err := dial(ctx, path)
	if err != nil {
		return err
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	}
	if err := send(conn, method, params); err != nil {
		return err
	}
	var resp Response
	if err := json.NewDecoder(io.LimitReader(conn, 16<<20)).Decode(&resp); err != nil {
		if closedEarly(err) {
			return busy(method)
		}
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

// dial connects to the socket at path, mapping the usual failures to codes.
func dial(ctx context.Context, path string) (net.Conn, error) {
	var d net.Dialer
	conn, err := d.DialContext(ctx, "unix", path)
	if err != nil {
		switch {
		case errors.Is(err, syscall.EACCES), errors.Is(err, syscall.EPERM):
			return nil, Errorf(CodeDenied, "cannot open %s: permission denied", path)
		case errors.Is(err, syscall.ENOENT), errors.Is(err, syscall.ECONNREFUSED):
			return nil, Errorf(CodeUnavailable, "nothing listens on %s", path)
		}
		return nil, Errorf(CodeUnavailable, "%v", err)
	}
	return conn, nil
}

// send writes one request.
func send(conn net.Conn, method string, params any) error {
	req := Request{Method: method}
	if params != nil {
		var err error
		if req.Params, err = json.Marshal(params); err != nil {
			return fmt.Errorf("%s: %w", method, err)
		}
	}
	if err := json.NewEncoder(conn).Encode(req); err != nil {
		if closedEarly(err) {
			return busy(method)
		}
		return fmt.Errorf("%s: send: %w", method, err)
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

// closedEarly reports an error from a server that closed the connection without
// answering, which it does when it is at its connection limit.
func closedEarly(err error) bool {
	return errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) ||
		errors.Is(err, syscall.EPIPE) || errors.Is(err, syscall.ECONNRESET)
}

func busy(method string) error {
	return Errorf(CodeBusy, "%s: the server closed the connection without answering (too many connections; try again)", method)
}
