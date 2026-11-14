// Package live fans stored events and dispatched commands out to connected
// application sessions on this replica.
package live

import (
	"errors"
	"sync"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
	"meteorcloud/dataplane/internal/streams"
)

// MaxSubscriptions bounds what one connection may hold.
const MaxSubscriptions = 64

var (
	ErrForbidden          = errors.New("subscription is outside this application's subscribe permissions")
	ErrTooMany            = errors.New("too many subscriptions on this connection")
	ErrInvalidPattern     = errors.New("invalid stream pattern")
	ErrDuplicateSubscribe = errors.New("subscription id is already in use")
)

// Sink is one connection's outbound side. Implementations must not block: a slow
// consumer is the sink's problem (it closes the connection), never the hub's.
type Sink interface {
	SendEvent(subscriptionID string, ev *envelope.Event)
	SendCommand(cmd *storage.Command)
}

// Subscription selects events of the connection's own organization.
type Subscription struct {
	ID          string
	Pattern     string
	Kind        string // optional
	DeviceID    string // optional
	Application string // optional application slug
}

func (s *Subscription) matches(ev *envelope.Event) bool {
	if s.Kind != "" && s.Kind != ev.Kind {
		return false
	}
	if s.DeviceID != "" && s.DeviceID != ev.DeviceID {
		return false
	}
	if s.Application != "" && s.Application != ev.Application {
		return false
	}
	return streams.Match(s.Pattern, ev.Stream)
}

// Conn is a registered connection.
type Conn struct {
	hub      *Hub
	Identity *identity.Identity
	sink     Sink
	mu       sync.RWMutex
	subs     map[string]*Subscription
	commands bool
}

// Hub indexes connections by organization.
type Hub struct {
	mu      sync.RWMutex
	byOrg   map[string]map[*Conn]struct{}
	metrics *observability.Metrics
}

func NewHub(metrics *observability.Metrics) *Hub {
	return &Hub{byOrg: map[string]map[*Conn]struct{}{}, metrics: metrics}
}

// Register adds a connection. Commands are delivered to it only when the application
// has the receive_commands permission.
func (h *Hub) Register(id *identity.Identity, sink Sink) *Conn {
	c := &Conn{hub: h, Identity: id, sink: sink, subs: map[string]*Subscription{},
		commands: id.Permissions.ReceiveCommands}
	h.mu.Lock()
	set := h.byOrg[id.OrganizationID]
	if set == nil {
		set = map[*Conn]struct{}{}
		h.byOrg[id.OrganizationID] = set
	}
	set[c] = struct{}{}
	h.mu.Unlock()
	h.metrics.LiveConnections.Inc()
	return c
}

// Close removes the connection and its subscriptions.
func (c *Conn) Close() {
	h := c.hub
	h.mu.Lock()
	if set, ok := h.byOrg[c.Identity.OrganizationID]; ok {
		if _, present := set[c]; present {
			delete(set, c)
			h.metrics.LiveConnections.Dec()
		}
		if len(set) == 0 {
			delete(h.byOrg, c.Identity.OrganizationID)
		}
	}
	h.mu.Unlock()
	c.mu.Lock()
	h.metrics.LiveSubscriptions.Sub(float64(len(c.subs)))
	c.subs = map[string]*Subscription{}
	c.mu.Unlock()
}

// Subscribe checks the pattern against the application's permissions once, here.
func (c *Conn) Subscribe(sub Subscription) error {
	if sub.ID == "" || len(sub.ID) > 64 {
		return ErrInvalidPattern
	}
	if err := streams.ValidatePattern(sub.Pattern); err != nil {
		return ErrInvalidPattern
	}
	if !c.Identity.CanSubscribe(sub.Pattern) {
		return ErrForbidden
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.subs[sub.ID]; ok {
		return ErrDuplicateSubscribe
	}
	if len(c.subs) >= MaxSubscriptions {
		return ErrTooMany
	}
	c.subs[sub.ID] = &sub
	c.hub.metrics.LiveSubscriptions.Inc()
	return nil
}

// Unsubscribe removes one subscription; unknown ids are ignored.
func (c *Conn) Unsubscribe(id string) {
	c.mu.Lock()
	if _, ok := c.subs[id]; ok {
		delete(c.subs, id)
		c.hub.metrics.LiveSubscriptions.Dec()
	}
	c.mu.Unlock()
}

func (h *Hub) conns(org string) []*Conn {
	h.mu.RLock()
	defer h.mu.RUnlock()
	set := h.byOrg[org]
	out := make([]*Conn, 0, len(set))
	for c := range set {
		out = append(out, c)
	}
	return out
}

// OnLive delivers a stored event to matching subscriptions of the same organization.
func (h *Hub) OnLive(ev *envelope.Event) {
	for _, c := range h.conns(ev.OrganizationID) {
		c.mu.RLock()
		for _, sub := range c.subs {
			if sub.matches(ev) {
				c.sink.SendEvent(sub.ID, ev)
				h.metrics.LiveDelivered.Inc()
			}
		}
		c.mu.RUnlock()
	}
}

// OnCommand delivers a dispatched command to connected instances of its target.
func (h *Hub) OnCommand(cmd *storage.Command) {
	for _, c := range h.conns(cmd.OrganizationID) {
		if !c.commands || c.Identity.ApplicationID != cmd.ApplicationID {
			continue
		}
		if cmd.DeviceID != "" && cmd.DeviceID != c.Identity.DeviceID {
			continue
		}
		c.sink.SendCommand(cmd)
	}
}
