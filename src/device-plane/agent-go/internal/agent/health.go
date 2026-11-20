package agent

import (
	"sort"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/api"
)

// State is a module's health state, as reported in capabilities.modules.
type State string

// Module states (contracts/schemas/agent/common.json#/$defs/module_health).
const (
	StateStarting State = "starting"
	StateRunning  State = "running"
	StateWaiting  State = "waiting"
	StateFailed   State = "failed"
	StateParked   State = "parked"
	StateStopped  State = "stopped"
)

// maxErrorLen matches the contract's limit on last_error.
const maxErrorLen = 500

// Health is the registry of module health. The supervisor sets states; modules
// call Tick on every loop so the watchdog can tell a live loop from a stuck one.
// The heartbeat sends Snapshot as capabilities.modules.
type Health struct {
	mu  sync.Mutex
	m   map[string]*moduleHealth
	now func() time.Time
}

type moduleHealth struct {
	critical bool
	state    State
	restarts int
	lastErr  string
	since    time.Time
	lastTick time.Time
}

// NewHealth returns an empty registry.
func NewHealth() *Health {
	return &Health{m: map[string]*moduleHealth{}, now: time.Now}
}

func (h *Health) entry(name string) *moduleHealth {
	e, ok := h.m[name]
	if !ok {
		e = &moduleHealth{state: StateStopped, since: h.now()}
		h.m[name] = e
	}
	return e
}

// register adds a module before it starts, so it shows up as starting.
func (h *Health) register(name string, critical bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	e := h.entry(name)
	e.critical = critical
}

// Set records a new state. err (may be nil) becomes last_error when not nil,
// redacted and cut to the contract's length.
func (h *Health) Set(name string, s State, err error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	e := h.entry(name)
	if e.state != s {
		e.state = s
		e.since = h.now()
	}
	if s == StateRunning {
		e.lastTick = h.now()
	}
	if err != nil {
		msg := Redact(err.Error())
		if len(msg) > maxErrorLen {
			msg = msg[:maxErrorLen]
		}
		e.lastErr = msg
	}
}

// failed records a failure and counts a restart.
func (h *Health) failed(name string, err error) {
	h.Set(name, StateFailed, err)
	h.mu.Lock()
	h.entry(name).restarts++
	h.mu.Unlock()
}

// Tick says the module's loop made progress. Critical modules must tick at least
// once a minute or the systemd watchdog restarts the agent.
func (h *Health) Tick(name string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.entry(name).lastTick = h.now()
}

// Get returns one module's health.
func (h *Health) Get(name string) (api.ModuleHealth, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	e, ok := h.m[name]
	if !ok {
		return api.ModuleHealth{}, false
	}
	return e.report(), true
}

// Snapshot returns every module's health for capabilities.modules.
func (h *Health) Snapshot() map[string]api.ModuleHealth {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make(map[string]api.ModuleHealth, len(h.m))
	for name, e := range h.m {
		out[name] = e.report()
	}
	return out
}

// Names lists the registered modules, sorted.
func (h *Health) Names() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]string, 0, len(h.m))
	for name := range h.m {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// Alive reports whether every running critical module ticked within maxAge.
// The watchdog pings systemd only while this is true. A module that waits (for
// enrollment, for example) has no loop to tick, so it does not count.
func (h *Health) Alive(maxAge time.Duration) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	now := h.now()
	for _, e := range h.m {
		if e.critical && e.state == StateRunning && now.Sub(e.lastTick) > maxAge {
			return false
		}
	}
	return true
}

func (e *moduleHealth) report() api.ModuleHealth {
	since := e.since.UTC()
	r := api.ModuleHealth{State: string(e.state), Restarts: e.restarts, Since: &since}
	if e.lastErr != "" {
		msg := e.lastErr
		r.LastError = &msg
	}
	return r
}
