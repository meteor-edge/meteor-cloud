package api

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"net/http"

	"github.com/coder/websocket"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/live"
	"meteorcloud/dataplane/internal/storage"
)

// Streaming protocol (JSON text frames), contracts/data-plane-v1.md.
//
// client -> server: {"op":"subscribe","id":"s1","stream":"energy.*","kind":"","device_id":"","application":""}
//
//	{"op":"unsubscribe","id":"s1"}   {"op":"ping"}
//
// server -> client: {"type":"ready","identity":{...}}
//
//	{"type":"subscribed","id":"s1"}  {"type":"unsubscribed","id":"s1"}
//	{"type":"event","subscription":"s1","event":{...}}
//	{"type":"command","command":{...}}
//	{"type":"error","id":"s1","code":"...","message":"..."}   {"type":"pong"}
const (
	sendBuffer        = 256
	wsReadLimit       = 64 << 10
	pingInterval      = 25 * time.Second
	revalidateEvery   = 10 * time.Second
	closeSlowConsumer = websocket.StatusCode(4008)
	closeUnauthorized = websocket.StatusCode(4401)
	closeGoingAway    = websocket.StatusGoingAway
)

type clientFrame struct {
	Op          string `json:"op"`
	ID          string `json:"id"`
	Stream      string `json:"stream"`
	Kind        string `json:"kind"`
	DeviceID    string `json:"device_id"`
	Application string `json:"application"`
}

type session struct {
	id     *identity.Identity
	out    chan []byte
	once   sync.Once
	closed chan struct{}
	reason websocket.StatusCode
	text   string
}

func (c *session) shutdown(code websocket.StatusCode, text string) {
	c.once.Do(func() {
		c.reason, c.text = code, text
		close(c.closed)
	})
}

// enqueue never blocks: a consumer that cannot keep up is disconnected and told why,
// so it can reconnect and backfill from history instead of silently losing events.
func (c *session) enqueue(frame any) {
	raw, err := json.Marshal(frame)
	if err != nil {
		return
	}
	select {
	case <-c.closed:
	case c.out <- raw:
	default:
		c.shutdown(closeSlowConsumer, "slow_consumer")
	}
}

func (c *session) SendEvent(subscriptionID string, ev *envelope.Event) {
	c.enqueue(map[string]any{"type": "event", "subscription": subscriptionID, "event": ev})
}

func (c *session) SendCommand(cmd *storage.Command) {
	c.enqueue(map[string]any{"type": "command", "command": cmd})
}

// handleStream upgrades to a WebSocket carrying live subscriptions and commands.
func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	s.wsMu.Lock()
	closing := s.closing
	s.wsMu.Unlock()
	if closing {
		w.Header().Set("Retry-After", "1")
		writeError(w, http.StatusServiceUnavailable, "shutting_down", "This instance is shutting down. Reconnect.")
		return
	}
	opts := &websocket.AcceptOptions{OriginPatterns: s.opts.AllowedWSOrigins}
	ws, err := websocket.Accept(w, r, opts)
	if err != nil {
		return // Accept already answered
	}
	ws.SetReadLimit(wsReadLimit)
	sess := &session{id: id, out: make(chan []byte, sendBuffer), closed: make(chan struct{})}
	s.wsMu.Lock()
	s.sessions[sess] = struct{}{}
	s.wsMu.Unlock()
	conn := s.hub.Register(id, sess)
	ctx, cancel := context.WithCancel(context.Background())
	defer func() {
		cancel()
		conn.Close()
		s.wsMu.Lock()
		delete(s.sessions, sess)
		s.wsMu.Unlock()
		s.lifecycle(id, "app.disconnected")
	}()

	sess.enqueue(map[string]any{"type": "ready", "identity": identityView(id)})
	// Commands that were waiting for this target.
	if pending, err := s.commands.Undelivered(ctx, id); err == nil {
		for _, cmd := range pending {
			sess.SendCommand(cmd)
		}
	}
	s.lifecycle(id, "app.connected")

	go s.readLoop(ctx, ws, sess, conn)

	ping := time.NewTicker(pingInterval)
	defer ping.Stop()
	revalidate := time.NewTicker(revalidateEvery)
	defer revalidate.Stop()
	for {
		select {
		case <-sess.closed:
			if sess.reason == closeSlowConsumer {
				s.metrics.LiveDropped.WithLabelValues("slow_consumer").Inc()
			}
			ws.Close(sess.reason, sess.text)
			return
		case frame := <-sess.out:
			wctx, wcancel := context.WithTimeout(ctx, 10*time.Second)
			err := ws.Write(wctx, websocket.MessageText, frame)
			wcancel()
			if err != nil {
				ws.CloseNow()
				return
			}
		case <-ping.C:
			pctx, pcancel := context.WithTimeout(ctx, 10*time.Second)
			err := ws.Ping(pctx)
			pcancel()
			if err != nil {
				ws.CloseNow()
				return
			}
		case <-revalidate.C:
			// Revocation and expiry apply to open connections, not only to new ones.
			if !s.policy.StillValid(id) {
				s.metrics.LiveDropped.WithLabelValues("credential_revoked").Inc()
				sess.shutdown(closeUnauthorized, "credential_revoked")
			}
		}
	}
}

func (s *Server) readLoop(ctx context.Context, ws *websocket.Conn, sess *session, conn *live.Conn) {
	for {
		_, raw, err := ws.Read(ctx)
		if err != nil {
			sess.shutdown(websocket.StatusNormalClosure, "")
			return
		}
		var frame clientFrame
		if json.Unmarshal(raw, &frame) != nil {
			sess.enqueue(map[string]any{"type": "error", "code": "invalid_frame", "message": "Frames must be JSON objects."})
			continue
		}
		switch frame.Op {
		case "subscribe":
			err := conn.Subscribe(live.Subscription{ID: frame.ID, Pattern: frame.Stream, Kind: frame.Kind,
				DeviceID: frame.DeviceID, Application: frame.Application})
			if err != nil {
				code := "invalid_subscription"
				if errors.Is(err, live.ErrForbidden) {
					code = "forbidden_stream"
				}
				sess.enqueue(map[string]any{"type": "error", "id": frame.ID, "code": code, "message": err.Error()})
				continue
			}
			sess.enqueue(map[string]any{"type": "subscribed", "id": frame.ID})
		case "unsubscribe":
			conn.Unsubscribe(frame.ID)
			sess.enqueue(map[string]any{"type": "unsubscribed", "id": frame.ID})
		case "ping":
			sess.enqueue(map[string]any{"type": "pong"})
		default:
			sess.enqueue(map[string]any{"type": "error", "id": frame.ID, "code": "unknown_op", "message": "Unknown op."})
		}
	}
}

// lifecycle records application connect/disconnect on the reserved stream
// "meteor.lifecycle" (kind "lifecycle"). Best effort: it never blocks a connection.
func (s *Server) lifecycle(id *identity.Identity, typ string) {
	if !s.opts.LifecycleEvents {
		return
	}
	now := time.Now().UTC()
	ev := &envelope.Event{ID: envelope.NewID(), SchemaVersion: envelope.SchemaVersion, Kind: envelope.KindLifecycle,
		Type: typ, Stream: "meteor.lifecycle", OrganizationID: id.OrganizationID, ApplicationID: id.ApplicationID,
		Application: id.Application, DeviceID: id.DeviceID, Environment: id.Environment, Time: now, ReceivedAt: now,
		Data: json.RawMessage(`{"transport":"websocket"}`)}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if _, err := s.pipeline.Submit(ctx, []*envelope.Event{ev}); err != nil {
			s.log.Debug("lifecycle event not stored", "error", err.Error())
		}
	}()
}

// CloseSessions tells every streaming client to reconnect elsewhere (graceful shutdown).
func (s *Server) CloseSessions() {
	s.wsMu.Lock()
	s.closing = true
	sessions := make([]*session, 0, len(s.sessions))
	for sess := range s.sessions {
		sessions = append(sessions, sess)
	}
	s.wsMu.Unlock()
	for _, sess := range sessions {
		sess.shutdown(closeGoingAway, "shutting_down")
	}
}
