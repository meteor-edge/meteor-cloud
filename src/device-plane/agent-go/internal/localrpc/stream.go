package localrpc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"time"
)

// Streams: a handler with Stream answers with any number of line frames
// ({"line": "..."}) and then the usual Response, so a caller sees log lines as
// they are written and still learns whether the call failed. The connection
// carries nothing else, so the server notices a caller that hangs up (the read
// side ends) and cancels the handler, which matters for followed logs that can
// stay quiet for hours.

// Stream limits.
const (
	// streamWriteTimeout bounds one frame write, so a caller that stops
	// reading frees its slot.
	streamWriteTimeout = 30 * time.Second
	// maxStreamLine cuts longer lines; the rest of the line is dropped.
	maxStreamLine = 64 << 10
)

// frame is one value on a streamed connection: a line, or the final Response.
type frame struct {
	Line *string `json:"line,omitempty"`
	Response
}

// stream authorizes and runs a streaming handler. The caller writes the final Response.
func (s *Server) stream(ctx context.Context, c *net.UnixConn, peer Peer, req Request) error {
	h, err := s.handler(peer, req.Method)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, s.handlerTimeout(h))
	defer cancel()

	// The caller sends nothing after its request: the read side ends only when
	// it hangs up (or we shut reading down below).
	_ = c.SetReadDeadline(time.Time{})
	hungUp := make(chan struct{})
	go func() { // owned: ended by CloseRead below at the latest
		defer close(hungUp)
		_, _ = io.Copy(io.Discard, c)
		cancel()
	}()
	w := &lineWriter{c: c}
	err = h.Stream(ctx, peer, req.Params, w)
	if ferr := w.flush(); err == nil && ferr != nil && ctx.Err() == nil {
		err = ferr
	}
	_ = c.CloseRead()
	<-hungUp
	_ = c.SetWriteDeadline(time.Now().Add(streamWriteTimeout))
	return err
}

// lineWriter turns what a handler writes into line frames.
type lineWriter struct {
	c       *net.UnixConn
	partial []byte // the current line, cut at maxStreamLine
	err     error
}

// Write implements io.Writer. After the caller is gone it returns the error,
// so a tool writing into it stops.
func (w *lineWriter) Write(p []byte) (int, error) {
	if w.err != nil {
		return 0, w.err
	}
	n := len(p)
	for len(p) > 0 {
		i := bytes.IndexByte(p, '\n')
		seg := p
		if i >= 0 {
			seg = p[:i]
		}
		if room := maxStreamLine - len(w.partial); room > 0 {
			w.partial = append(w.partial, seg[:min(len(seg), room)]...)
		}
		if i < 0 {
			break
		}
		if err := w.send(); err != nil {
			return 0, err
		}
		p = p[i+1:]
	}
	return n, nil
}

func (w *lineWriter) send() error {
	line := string(w.partial)
	w.partial = w.partial[:0]
	_ = w.c.SetWriteDeadline(time.Now().Add(streamWriteTimeout))
	if err := json.NewEncoder(w.c).Encode(frame{Line: &line}); err != nil {
		w.err = fmt.Errorf("caller went away: %w", err)
		return w.err
	}
	return nil
}

// flush sends an unterminated last line.
func (w *lineWriter) flush() error {
	if w.err != nil {
		return w.err
	}
	if len(w.partial) > 0 {
		return w.send()
	}
	return nil
}

// CallStream calls a streaming method and hands each line to line as it
// arrives, without a newline. It returns when the server ends the stream (the
// method's error, if any), when line returns an error, or when ctx ends.
// Unlike Call it sets no deadline of its own: a followed log has no end.
func CallStream(ctx context.Context, path, method string, params any, line func(string) error) error {
	conn, err := dial(ctx, path)
	if err != nil {
		return err
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { _ = conn.SetDeadline(time.Now()) })
	defer stop()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	}
	if err := send(conn, method, params); err != nil {
		return err
	}
	dec := json.NewDecoder(conn)
	for {
		var f frame
		if err := dec.Decode(&f); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if closedEarly(err) {
				return busy(method)
			}
			return fmt.Errorf("%s: %w", method, err)
		}
		if f.Line != nil {
			if err := line(*f.Line); err != nil {
				return err
			}
			continue
		}
		if !f.OK {
			if f.Error == nil {
				return Errorf(CodeFailed, "%s failed", method)
			}
			return f.Error
		}
		return nil
	}
}

// IsHangup reports an error from writing to a caller that went away.
func IsHangup(err error) bool {
	return errors.Is(err, net.ErrClosed) || closedEarly(err)
}
