package agent

import "sync"

// Signal is an event one module tells the others about. Signals with the same
// Key coalesce: a subscriber that is busy sees only the latest one.
type Signal interface {
	Key() string
}

// Enrolled says the device has an identity now; modules waiting for one start.
type Enrolled struct{}

// Key implements Signal.
func (Enrolled) Key() string { return "enrolled" }

// WorkWaiting says the server has work of Kind ("actions", "wifi", "software").
type WorkWaiting struct{ Kind string }

// Key implements Signal. Each kind coalesces on its own.
func (w WorkWaiting) Key() string { return "work_waiting:" + w.Kind }

// CredentialsRotated says the device token or MQTT password changed.
type CredentialsRotated struct{}

// Key implements Signal.
func (CredentialsRotated) Key() string { return "credentials_rotated" }

// ConfigChanged says config.json changed. It also unparks parked modules.
type ConfigChanged struct{}

// Key implements Signal.
func (ConfigChanged) Key() string { return "config_changed" }

// Signals is the hub modules use to cooperate without calling each other.
// Publish never blocks: a slow subscriber only loses older signals of a key.
type Signals struct {
	mu   sync.Mutex
	subs map[*Subscription]struct{}
}

// NewSignals returns an empty hub.
func NewSignals() *Signals { return &Signals{subs: map[*Subscription]struct{}{}} }

// Subscription receives the signals its filter accepts. Wait on C, then call Take.
type Subscription struct {
	hub     *Signals
	filter  func(Signal) bool
	c       chan struct{}
	mu      sync.Mutex
	pending map[string]Signal
	order   []string
}

// Subscribe returns a subscription for the signals filter accepts (all when nil).
// Close it when the module stops.
func (s *Signals) Subscribe(filter func(Signal) bool) *Subscription {
	sub := &Subscription{hub: s, filter: filter, c: make(chan struct{}, 1), pending: map[string]Signal{}}
	s.mu.Lock()
	s.subs[sub] = struct{}{}
	s.mu.Unlock()
	return sub
}

// Publish delivers sig to every matching subscriber without blocking.
func (s *Signals) Publish(sig Signal) {
	s.mu.Lock()
	subs := make([]*Subscription, 0, len(s.subs))
	for sub := range s.subs {
		subs = append(subs, sub)
	}
	s.mu.Unlock()
	for _, sub := range subs {
		if sub.filter == nil || sub.filter(sig) {
			sub.offer(sig)
		}
	}
}

func (sub *Subscription) offer(sig Signal) {
	sub.mu.Lock()
	key := sig.Key()
	if _, ok := sub.pending[key]; !ok {
		sub.order = append(sub.order, key)
	}
	sub.pending[key] = sig
	sub.mu.Unlock()
	select {
	case sub.c <- struct{}{}:
	default: // already notified
	}
}

// C is ready when Take has something to return.
func (sub *Subscription) C() <-chan struct{} { return sub.c }

// Take returns the pending signals (latest per key, in first-arrival order) and clears them.
func (sub *Subscription) Take() []Signal {
	sub.mu.Lock()
	defer sub.mu.Unlock()
	out := make([]Signal, 0, len(sub.order))
	for _, k := range sub.order {
		out = append(out, sub.pending[k])
	}
	sub.pending = map[string]Signal{}
	sub.order = nil
	return out
}

// Close stops delivery to this subscription.
func (sub *Subscription) Close() {
	sub.hub.mu.Lock()
	delete(sub.hub.subs, sub)
	sub.hub.mu.Unlock()
}

// Is returns a filter that accepts signals of the same type as example
// (for WorkWaiting: the same Kind).
func Is(example Signal) func(Signal) bool {
	key := example.Key()
	return func(s Signal) bool { return s.Key() == key }
}
