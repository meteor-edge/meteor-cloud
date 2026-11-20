// Package fakecp is a scripted control plane for tests: each route answers
// with the replies it was given, in order (the last one repeats), and every
// request is recorded. Unscripted routes answer 404 with the contract's error
// body, like the fake control plane of the contract suite. Only tests import it.
package fakecp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// Reply is one scripted answer.
type Reply struct {
	Status int
	Body   any
	Header map[string]string
}

// OK is a 200 with body.
func OK(body any) Reply { return Reply{Status: http.StatusOK, Body: body} }

// Created is a 201 with body.
func Created(body any) Reply { return Reply{Status: http.StatusCreated, Body: body} }

// Fail is an error answer with the contract's error body and optional headers
// as name, value pairs.
func Fail(status int, code string, header ...string) Reply {
	r := Reply{Status: status, Body: map[string]any{"error": map[string]string{"code": code, "message": code}}}
	if len(header) > 1 {
		r.Header = map[string]string{}
		for i := 0; i+1 < len(header); i += 2 {
			r.Header[header[i]] = header[i+1]
		}
	}
	return r
}

// Call is one recorded request.
type Call struct {
	Method string
	Path   string
	Header http.Header
	Body   map[string]any
	At     time.Time
}

// Server is the fake control plane.
type Server struct {
	*httptest.Server

	mu     sync.Mutex
	routes map[string][]Reply
	calls  []Call
	notify chan struct{}
}

// New starts a fake control plane that stops when the test ends.
func New(t testing.TB) *Server {
	s := &Server{routes: map[string][]Reply{}, notify: make(chan struct{})}
	s.Server = httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(s.Close)
	return s
}

// On scripts method path to answer with replies in order; the last repeats.
func (s *Server) On(method, path string, replies ...Reply) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.routes[method+" "+path] = replies
}

// Calls returns the recorded requests to method path.
func (s *Server) Calls(method, path string) []Call {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Call
	for _, c := range s.calls {
		if c.Method == method && c.Path == path {
			out = append(out, c)
		}
	}
	return out
}

// Wait blocks until method path was called n times, or fails the test after timeout.
func (s *Server) Wait(t testing.TB, method, path string, n int, timeout time.Duration) []Call {
	t.Helper()
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	for {
		s.mu.Lock()
		ch := s.notify
		s.mu.Unlock()
		if calls := s.Calls(method, path); len(calls) >= n {
			return calls
		}
		select {
		case <-ch:
		case <-deadline.C:
			t.Fatalf("%s %s called %d times within %s, want %d", method, path, len(s.Calls(method, path)), timeout, n)
			return nil
		}
	}
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	data, _ := io.ReadAll(r.Body)
	var body map[string]any
	_ = json.Unmarshal(data, &body)

	s.mu.Lock()
	key := r.Method + " " + r.URL.Path
	reply := Fail(http.StatusNotFound, "not_found")
	if q := s.routes[key]; len(q) > 0 {
		reply = q[0]
		if len(q) > 1 {
			s.routes[key] = q[1:]
		}
	}
	s.calls = append(s.calls, Call{Method: r.Method, Path: r.URL.Path, Header: r.Header.Clone(), Body: body, At: time.Now()})
	close(s.notify)
	s.notify = make(chan struct{})
	s.mu.Unlock()

	for k, v := range reply.Header {
		w.Header().Set(k, v)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(reply.Status)
	if reply.Body != nil {
		_ = json.NewEncoder(w).Encode(reply.Body)
	}
}
