package agent

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"runtime/debug"
	"sync"
	"time"
)

// Supervisor defaults. Tests shorten them; the agent uses these.
const (
	DefaultBackoffMin    = time.Second
	DefaultBackoffMax    = 5 * time.Minute
	DefaultHealthyAfter  = 10 * time.Minute
	DefaultBreakerLimit  = 5
	DefaultBreakerWindow = 10 * time.Minute
	DefaultParkFor       = time.Hour
	DefaultMaxTickAge    = 3 * time.Minute
)

// ErrUnknownModule is returned by Restart for a name no module has.
var ErrUnknownModule = errors.New("unknown module")

// errReturnedEarly is the failure recorded when Run returns nil before shutdown.
var errReturnedEarly = errors.New("returned before shutdown")

// Supervisor runs every module in its own goroutine, turns panics into errors,
// restarts failed modules with backoff, parks optional modules that crash-loop,
// and keeps the systemd watchdog fed while the critical loops tick. A failing
// module never stops the others.
type Supervisor struct {
	// Restart backoff: BackoffMin doubling to BackoffMax, reset after a run
	// that lasted HealthyAfter.
	BackoffMin, BackoffMax, HealthyAfter time.Duration
	// BreakerLimit failures within BreakerWindow park an optional module for ParkFor.
	BreakerLimit  int
	BreakerWindow time.Duration
	ParkFor       time.Duration
	// Notifier talks to systemd; nil or unconfigured means no notifications.
	Notifier *Notifier
	// MaxTickAge is how old a critical module's last Tick may be before the
	// watchdog stops pinging systemd.
	MaxTickAge time.Duration
	// Enrolled, when set, holds back the modules given to NewSupervisor until
	// it returns true or an Enrolled signal arrives (they show as waiting).
	// Modules added with Add (socket, identity) start at once: the device must
	// be manageable and enrollable before it has an identity.
	Enrolled func() bool

	modules []Module
	core    map[Module]bool
	deps    Deps
	gate    chan struct{} // closed once the device is enrolled

	mu      sync.Mutex
	runners map[string]*runner
}

// runner is the supervisor's bookkeeping for one module.
type runner struct {
	mod   Module
	gated bool          // waits for enrollment before the first run
	wake  chan struct{} // unpark, or end a backoff early

	mu        sync.Mutex
	cancel    context.CancelFunc // cancels the current Run
	requested bool               // the current Run was cancelled by Restart
	pending   bool               // Restart came while no Run was active
}

// NewSupervisor prepares modules to run with d. Every module gets its own copy of
// d whose Log carries module=<name>.
func NewSupervisor(modules []Module, d Deps) *Supervisor {
	return &Supervisor{
		BackoffMin: DefaultBackoffMin, BackoffMax: DefaultBackoffMax, HealthyAfter: DefaultHealthyAfter,
		BreakerLimit: DefaultBreakerLimit, BreakerWindow: DefaultBreakerWindow, ParkFor: DefaultParkFor,
		MaxTickAge: DefaultMaxTickAge,
		modules:    modules, deps: d, runners: map[string]*runner{}, core: map[Module]bool{},
	}
}

// Add registers a core module that starts without waiting for enrollment (the
// socket and identity modules). Call it before Run.
func (s *Supervisor) Add(m Module) {
	s.modules = append(s.modules, m)
	s.core[m] = true
}

// Run starts all modules and blocks until ctx is cancelled and every module has
// returned. It tells systemd READY=1 once the modules are started.
func (s *Supervisor) Run(ctx context.Context) error {
	log := s.deps.Log.With("module", "supervisor")
	var wg sync.WaitGroup

	sub := s.deps.Signals.Subscribe(Is(ConfigChanged{}))
	defer sub.Close()
	// Subscribe before asking Enrolled, so an enrollment in between is not missed.
	enrolled := s.deps.Signals.Subscribe(Is(Enrolled{}))
	defer enrolled.Close()
	s.gate = make(chan struct{})
	if s.Enrolled == nil || s.Enrolled() {
		close(s.gate)
	}

	s.mu.Lock()
	for _, m := range s.modules {
		name := m.Name()
		if _, dup := s.runners[name]; dup {
			s.mu.Unlock()
			return fmt.Errorf("supervisor: module %q registered twice", name)
		}
		r := &runner{mod: m, gated: !s.core[m], wake: make(chan struct{}, 1)}
		s.runners[name] = r
		s.deps.Health.register(name, m.Critical())
		s.deps.Health.Set(name, StateStarting, nil)
	}
	for _, r := range s.runners {
		wg.Add(1)
		go func() {
			defer wg.Done()
			s.supervise(ctx, r)
		}()
	}
	s.mu.Unlock()

	if every := s.Notifier.WatchdogInterval(); every > 0 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			s.feedWatchdog(ctx, every/2)
		}()
	}
	if err := s.Notifier.Ready(ctx); err != nil {
		log.Warn("systemd notify failed", "err", err)
	}
	log.Info("modules started", "count", len(s.modules))

	for {
		select {
		case <-ctx.Done():
			if err := s.Notifier.Stopping(context.Background()); err != nil {
				log.Warn("systemd notify failed", "err", err)
			}
			wg.Wait()
			log.Info("all modules stopped")
			return nil
		case <-sub.C():
			sub.Take()
			s.wakeAll()
		case <-enrolled.C():
			enrolled.Take()
			select {
			case <-s.gate:
			default:
				log.Info("device enrolled; starting all modules")
				close(s.gate) // only this goroutine closes it
			}
		}
	}
}

// Restart restarts a running module, or unparks a parked one. It backs
// `meteorcli module restart <name>` and the restart_module action.
func (s *Supervisor) Restart(name string) error {
	s.mu.Lock()
	r, ok := s.runners[name]
	s.mu.Unlock()
	if !ok {
		return fmt.Errorf("restart %s: %w", name, ErrUnknownModule)
	}
	r.mu.Lock()
	if r.cancel != nil {
		r.requested = true
		r.cancel()
	} else {
		// Between runs (backoff, parked, or about to start): the next run is the
		// restart. runOnce consumes this when it starts.
		r.pending = true
	}
	r.mu.Unlock()
	r.poke()
	return nil
}

// wakeAll unparks every parked module (config changed).
func (s *Supervisor) wakeAll() {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, r := range s.runners {
		r.poke()
	}
}

func (r *runner) poke() {
	select {
	case r.wake <- struct{}{}:
	default:
	}
}

// supervise is one module's life: run, and after a failure wait and run again.
func (s *Supervisor) supervise(ctx context.Context, r *runner) {
	name := r.mod.Name()
	health := s.deps.Health
	log := s.deps.Log.With("module", name)
	breaker := Breaker{Limit: s.BreakerLimit, Window: s.BreakerWindow}
	delay := restartDelay{min: s.BackoffMin, max: s.BackoffMax}

	if r.gated && !s.waitForEnrollment(ctx, name) {
		health.Set(name, StateStopped, nil)
		return
	}
	for {
		// A poke from before this run (a config change while running) means nothing now.
		select {
		case <-r.wake:
		default:
		}
		started := time.Now()
		err := s.runOnce(ctx, r, log)
		if ctx.Err() != nil {
			health.Set(name, StateStopped, nil)
			return
		}
		if r.takeRequested() {
			log.Info("restarting on request")
			health.Set(name, StateStarting, nil)
			breaker.Reset()
			delay.reset()
			continue
		}
		if err == nil {
			err = errReturnedEarly
		}
		if time.Since(started) >= s.HealthyAfter {
			breaker.Reset()
			delay.reset()
		}
		health.failed(name, err)
		tripped := breaker.Record(time.Now())

		if tripped && !r.mod.Critical() {
			log.Error("module parked after repeated failures", "err", err,
				"failures", s.BreakerLimit, "window", s.BreakerWindow, "park_for", s.ParkFor)
			health.Set(name, StateParked, nil)
			if !wait(ctx, r.wake, s.ParkFor) {
				health.Set(name, StateStopped, nil)
				return
			}
			log.Info("module unparked")
			breaker.Reset()
			delay.reset()
			health.Set(name, StateStarting, nil)
			continue
		}

		backoff := delay.take()
		log.Error("module failed, restarting", "err", err, "in", backoff)
		if !wait(ctx, r.wake, backoff) {
			health.Set(name, StateStopped, nil)
			return
		}
		health.Set(name, StateStarting, nil)
	}
}

// waitForEnrollment holds a feature module back until the device is enrolled.
// It returns false when ctx ended first.
func (s *Supervisor) waitForEnrollment(ctx context.Context, name string) bool {
	select {
	case <-s.gate:
		return true
	default:
	}
	s.deps.Health.Set(name, StateWaiting, nil)
	select {
	case <-ctx.Done():
		return false
	case <-s.gate:
		s.deps.Health.Set(name, StateStarting, nil)
		return true
	}
}

// runOnce calls Run with a context Restart can cancel and turns a panic into an error.
func (s *Supervisor) runOnce(ctx context.Context, r *runner, log *slog.Logger) (err error) {
	name := r.mod.Name()
	runCtx, cancel := context.WithCancel(ctx)
	r.mu.Lock()
	r.cancel = cancel
	if r.pending {
		// A Restart arrived before this run: starting it fulfils the request.
		r.pending = false
		select {
		case <-r.wake:
		default:
		}
	}
	r.mu.Unlock()
	defer func() {
		r.mu.Lock()
		r.cancel = nil
		r.mu.Unlock()
		cancel()
		if p := recover(); p != nil {
			log.Error("module panicked", "panic", fmt.Sprint(p), "stack", string(debug.Stack()))
			err = fmt.Errorf("panic: %v", p)
		}
	}()

	d := s.deps
	d.Log = s.deps.Log.With("module", name)
	s.deps.Health.Set(name, StateRunning, nil)
	if err := r.mod.Run(runCtx, d); err != nil && !errors.Is(err, context.Canceled) {
		return fmt.Errorf("%s: %w", name, err)
	}
	return nil // a nil return before shutdown is caught by supervise
}

func (r *runner) takeRequested() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	req := r.requested
	r.requested = false
	return req
}

// wait sleeps for d, or until wake fires. It returns false when ctx ended.
func wait(ctx context.Context, wake <-chan struct{}, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-wake:
		return true
	case <-t.C:
		return true
	}
}

// feedWatchdog pings systemd while the critical loops tick. When one stalls the
// pings stop and systemd restarts the agent (WatchdogSec in the unit).
func (s *Supervisor) feedWatchdog(ctx context.Context, every time.Duration) {
	log := s.deps.Log.With("module", "supervisor")
	t := time.NewTicker(every)
	defer t.Stop()
	stalled := false
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if !s.deps.Health.Alive(s.MaxTickAge) {
			if !stalled {
				log.Error("a critical module stopped ticking; letting the systemd watchdog restart the agent")
				stalled = true
			}
			continue
		}
		stalled = false
		if err := s.Notifier.Watchdog(ctx); err != nil {
			log.Warn("systemd watchdog ping failed", "err", err)
		}
	}
}
