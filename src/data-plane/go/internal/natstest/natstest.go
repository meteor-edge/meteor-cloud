// Package natstest runs a real NATS server with JetStream inside a test
// process. It is the same nats-server code that runs in production, embedded,
// so integration tests need no Docker. Only tests import it.
package natstest

import (
	"fmt"
	"net"
	"testing"
	"time"

	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Server is a running JetStream server that can be restarted on the same port
// and storage directory.
type Server struct {
	t        *testing.T
	opts     *server.Options
	S        *server.Server
	StoreDir string
}

// Start runs a single JetStream server with file storage in a temp directory.
func Start(t *testing.T) *Server {
	t.Helper()
	dir := t.TempDir()
	return start(t, &server.Options{Host: "127.0.0.1", Port: -1, JetStream: true, StoreDir: dir, NoLog: true, NoSigs: true}, dir)
}

// StartConfig runs a server from a nats.conf file ($VARS come from the
// environment), with the port and storage directory replaced for the test.
func StartConfig(t *testing.T, path string) *Server {
	t.Helper()
	opts, err := server.ProcessConfigFile(path)
	if err != nil {
		t.Fatalf("nats config: %v", err)
	}
	dir := t.TempDir()
	opts.Host, opts.Port, opts.HTTPPort, opts.StoreDir = "127.0.0.1", -1, 0, dir
	opts.NoLog, opts.NoSigs = true, true
	return start(t, opts, dir)
}

func start(t *testing.T, opts *server.Options, dir string) *Server {
	s, err := server.NewServer(opts)
	if err != nil {
		t.Fatalf("nats server: %v", err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("nats server did not start")
	}
	srv := &Server{t: t, opts: opts, S: s, StoreDir: dir}
	// Later restarts reuse the port that was picked.
	opts.Port = s.Addr().(*net.TCPAddr).Port
	t.Cleanup(func() { srv.S.Shutdown(); srv.S.WaitForShutdown() })
	return srv
}

// URL is the client URL (stable across restarts).
func (s *Server) URL() string { return fmt.Sprintf("nats://127.0.0.1:%d", s.opts.Port) }

// Restart stops the server and starts it again on the same port and storage.
func (s *Server) Restart() {
	s.t.Helper()
	s.S.Shutdown()
	s.S.WaitForShutdown()
	opts := s.opts.Clone()
	ns, err := server.NewServer(opts)
	if err != nil {
		s.t.Fatalf("nats restart: %v", err)
	}
	go ns.Start()
	if !ns.ReadyForConnections(10 * time.Second) {
		s.t.Fatal("nats server did not restart")
	}
	s.S = ns
}

// Stop shuts the server down (simulates an outage).
func (s *Server) Stop() {
	s.S.Shutdown()
	s.S.WaitForShutdown()
}

// Connect returns a client connection and its JetStream context.
func (s *Server) Connect(t *testing.T, opts ...nats.Option) (*nats.Conn, jetstream.JetStream) {
	t.Helper()
	nc, err := nats.Connect(s.URL(), opts...)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	t.Cleanup(nc.Close)
	js, err := jetstream.New(nc)
	if err != nil {
		t.Fatal(err)
	}
	return nc, js
}
