package bus

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/eclipse/paho.golang/paho"
)

// ErrNotConnected is returned by Publish while there is no broker connection.
// Callers skip the message (metrics) or tell the user (mqtt-test); the session
// reconnects by itself.
var ErrNotConnected = errors.New("not connected to the MQTT broker")

// publishTimeout bounds one publish, including the broker's acknowledgement.
const publishTimeout = 10 * time.Second

// recentSize is how many received messages Recent can return (mqtt-listen).
const recentSize = 64

// Message is one message received on a subscribed topic.
type Message struct {
	// Seq counts received messages from 1, so a listener can ask for the ones after.
	Seq     uint64    `json:"seq"`
	Topic   string    `json:"topic"`
	Payload string    `json:"payload"`
	At      time.Time `json:"at"`
}

// publisher is the part of a paho client the router uses (a fake in tests).
type publisher interface {
	Publish(ctx context.Context, p *paho.Publish) (*paho.PublishResponse, error)
}

// State is the connection as `meteorcli status` and mqtt-test show it.
type State struct {
	Connected bool      `json:"connected"`
	Broker    string    `json:"broker,omitempty"`
	Since     time.Time `json:"since"`
	LastError string    `json:"last_error,omitempty"`
}

// Router is the one way modules use MQTT. It always exists (in agent.Deps);
// the mqtt module runs the session behind it with Run. Safe for concurrent use.
type Router struct {
	log *slog.Logger

	mu       sync.Mutex
	pub      publisher // nil while disconnected
	deviceID string
	state    State
	up       chan struct{} // closed while connected; replaced on disconnect
	inboxes  map[*inbox]struct{}
	recent   []Message
	seq      uint64
	arrived  chan struct{} // closed when a message arrives; replaced
}

// inbox is one subscriber of a suffix.
type inbox struct {
	suffix string
	c      chan Message
}

// NewRouter returns a router with no connection. log should carry module=bus.
func NewRouter(log *slog.Logger) *Router {
	return &Router{
		log:     log,
		up:      make(chan struct{}),
		inboxes: map[*inbox]struct{}{},
		arrived: make(chan struct{}),
		state:   State{Since: time.Now()},
	}
}

// Publish sends payload on devices/{id}/<suffix> with QoS 1 (every topic of
// the contract is QoS 1) and waits for the broker's acknowledgement, at most
// publishTimeout. It fails with ErrNotConnected while disconnected: nothing is
// queued, so a long outage cannot fill the memory.
func (r *Router) Publish(ctx context.Context, suffix string, payload []byte, retain bool) error {
	if !publishable[suffix] {
		return fmt.Errorf("bus: a device may not publish on %q", suffix)
	}
	r.mu.Lock()
	pub, id := r.pub, r.deviceID
	r.mu.Unlock()
	if pub == nil {
		return ErrNotConnected
	}
	ctx, cancel := context.WithTimeout(ctx, publishTimeout)
	defer cancel()
	if _, err := pub.Publish(ctx, &paho.Publish{Topic: Topic(id, suffix), QoS: 1, Retain: retain, Payload: payload}); err != nil {
		return fmt.Errorf("bus: publish %s: %w", suffix, err)
	}
	return nil
}

// PublishJSON is Publish with v encoded as JSON.
func (r *Router) PublishJSON(ctx context.Context, suffix string, v any, retain bool) error {
	data, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("bus: encode %s: %w", suffix, err)
	}
	return r.Publish(ctx, suffix, data, retain)
}

// Inbox returns a channel of the messages received on suffix, and a function
// that stops delivery. A full inbox drops the message (with a warning) rather
// than block the connection: the reader is a module's own loop.
func (r *Router) Inbox(suffix string, size int) (<-chan Message, func()) {
	in := &inbox{suffix: suffix, c: make(chan Message, max(size, 1))}
	r.mu.Lock()
	r.inboxes[in] = struct{}{}
	r.mu.Unlock()
	return in.c, func() {
		r.mu.Lock()
		delete(r.inboxes, in)
		r.mu.Unlock()
	}
}

// Up returns a channel that is closed while the session is connected. Take a
// new one after each disconnect.
func (r *Router) Up() <-chan struct{} {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.up
}

// State returns the connection state.
func (r *Router) State() State {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.state
}

// Recent returns the received messages with Seq > after, waiting up to wait
// for one when there are none yet, and the Seq to ask after next time. With
// fromNow it only returns that Seq (start listening now). mqtt-listen polls it
// over the agent socket, so the CLI never needs the broker password.
func (r *Router) Recent(ctx context.Context, after uint64, fromNow bool, wait time.Duration) ([]Message, uint64) {
	timer := time.NewTimer(wait)
	defer timer.Stop()
	for {
		r.mu.Lock()
		if fromNow {
			seq := r.seq
			r.mu.Unlock()
			return nil, seq
		}
		var out []Message
		for _, m := range r.recent {
			if m.Seq > after {
				out = append(out, m)
			}
		}
		seq, arrived := r.seq, r.arrived
		r.mu.Unlock()
		if len(out) > 0 {
			return out, seq
		}
		select {
		case <-arrived:
		case <-timer.C:
			return nil, max(seq, after)
		case <-ctx.Done():
			return nil, max(seq, after)
		}
	}
}

// deliver hands a received message to the inboxes of its suffix and keeps it
// for Recent. It runs on the connection's goroutine and never blocks.
func (r *Router) deliver(topic string, payload []byte) {
	r.mu.Lock()
	prefix := "devices/" + r.deviceID + "/"
	suffix := ""
	if len(topic) > len(prefix) && topic[:len(prefix)] == prefix {
		suffix = topic[len(prefix):]
	}
	if !subscribable[suffix] {
		r.mu.Unlock()
		r.log.Warn("ignored a message on a topic this device does not subscribe to", "topic", topic)
		return
	}
	r.seq++
	m := Message{Seq: r.seq, Topic: topic, Payload: string(payload), At: time.Now().UTC()}
	r.recent = append(r.recent, m)
	if len(r.recent) > recentSize {
		r.recent = r.recent[len(r.recent)-recentSize:]
	}
	close(r.arrived)
	r.arrived = make(chan struct{})
	var dropped int
	for in := range r.inboxes {
		if in.suffix != suffix {
			continue
		}
		select {
		case in.c <- m:
		default:
			dropped++
		}
	}
	r.mu.Unlock()
	if dropped > 0 {
		r.log.Warn("inbox full; dropped a received message", "topic", topic)
	}
}

// setUp records a working connection.
func (r *Router) setUp(pub publisher, deviceID, broker string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pub, r.deviceID = pub, deviceID
	r.state = State{Connected: true, Broker: broker, Since: time.Now().UTC()}
	select {
	case <-r.up: // already closed
	default:
		close(r.up)
	}
}

// setDown records that the connection ended (err nil: a clean stop).
func (r *Router) setDown(broker string, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	wasUp := r.pub != nil
	r.pub = nil
	st := State{Broker: broker, Since: r.state.Since, LastError: r.state.LastError}
	if wasUp || r.state.Connected {
		st.Since = time.Now().UTC()
	}
	if err != nil {
		st.LastError = err.Error()
	}
	r.state = st
	if wasUp {
		r.up = make(chan struct{})
	}
}

// setDeviceID lets deliver route messages before the first connect finished.
func (r *Router) setDeviceID(id string) {
	r.mu.Lock()
	r.deviceID = id
	r.mu.Unlock()
}
