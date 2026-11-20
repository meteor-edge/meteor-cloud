package agent

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/identity"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeModule runs a function as a module.
type fakeModule struct {
	name     string
	critical bool
	runs     atomic.Int32
	run      func(ctx context.Context, d Deps, n int32) error
}

func (m *fakeModule) Name() string   { return m.name }
func (m *fakeModule) Critical() bool { return m.critical }
func (m *fakeModule) Run(ctx context.Context, d Deps) error {
	return m.run(ctx, d, m.runs.Add(1))
}

// ticker is a healthy module that counts loop iterations.
func ticker(name string, count *atomic.Int64) *fakeModule {
	return &fakeModule{name: name, critical: true, run: func(ctx context.Context, d Deps, _ int32) error {
		t := time.NewTicker(time.Millisecond)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-t.C:
				count.Add(1)
				d.Health.Tick(name)
			}
		}
	}}
}

// syncBuffer is a goroutine-safe log sink.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func testDeps(w io.Writer) Deps {
	if w == nil {
		w = io.Discard
	}
	return Deps{Signals: NewSignals(), Health: NewHealth(), Log: NewLogger(w, slog.LevelDebug), Identity: identity.NewSource(config.Paths{})}
}

// fast shortens every supervisor delay so the tests run in milliseconds.
func fast(s *Supervisor) *Supervisor {
	s.BackoffMin, s.BackoffMax, s.HealthyAfter = time.Millisecond, 4*time.Millisecond, time.Hour
	s.BreakerWindow, s.ParkFor = time.Minute, time.Hour
	return s
}

func start(t *testing.T, s *Supervisor) (stop func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	return func() {
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("Run: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("supervisor did not stop")
		}
	}
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("timed out waiting for: %s", what)
}

func TestPanickingModuleRestartsWhileOthersKeepTicking(t *testing.T) {
	var ticks atomic.Int64
	logs := &syncBuffer{}
	healthy := ticker("heartbeat", &ticks)
	crashy := &fakeModule{name: "deploy", critical: true, run: func(ctx context.Context, d Deps, n int32) error {
		time.Sleep(2 * time.Millisecond)
		panic("boom")
	}}
	d := testDeps(logs)
	s := fast(NewSupervisor([]Module{healthy, crashy}, d))
	stop := start(t, s)

	eventually(t, "deploy restarted 5 times", func() bool { return crashy.runs.Load() >= 5 })
	before := ticks.Load()
	eventually(t, "heartbeat keeps ticking", func() bool { return ticks.Load() > before+10 })
	stop()

	hb, _ := d.Health.Get("heartbeat")
	if hb.Restarts != 0 || hb.State != string(StateStopped) {
		t.Errorf("heartbeat health %+v", hb)
	}
	dp, _ := d.Health.Get("deploy")
	if dp.Restarts < 4 || dp.LastError == nil || *dp.LastError != "panic: boom" {
		t.Errorf("deploy health %+v", dp)
	}
	if healthy.runs.Load() != 1 {
		t.Errorf("heartbeat was restarted: %d runs", healthy.runs.Load())
	}
	if !bytes.Contains([]byte(logs.String()), []byte("module=deploy")) {
		t.Error("log lines lack module=deploy")
	}
}

func TestCrashLoopParksAndConfigChangedUnparks(t *testing.T) {
	failing := &fakeModule{name: "telemetry", run: func(ctx context.Context, d Deps, n int32) error {
		return errors.New("sensor missing")
	}}
	d := testDeps(nil)
	s := fast(NewSupervisor([]Module{failing}, d))
	stop := start(t, s)
	defer stop()

	state := func() string { h, _ := d.Health.Get("telemetry"); return h.State }
	eventually(t, "parked", func() bool { return state() == string(StateParked) })
	if got := failing.runs.Load(); got != 5 {
		t.Errorf("runs before parking = %d, want 5", got)
	}
	time.Sleep(20 * time.Millisecond)
	if got := failing.runs.Load(); got != 5 || state() != string(StateParked) {
		t.Fatalf("parked module ran again: runs %d state %s", got, state())
	}

	d.Signals.Publish(ConfigChanged{})
	eventually(t, "runs again after ConfigChanged", func() bool { return failing.runs.Load() > 5 })
	eventually(t, "parked again", func() bool { return state() == string(StateParked) && failing.runs.Load() == 10 })
}

func TestParkedModuleUnparksAfterParkFor(t *testing.T) {
	failing := &fakeModule{name: "telemetry", run: func(context.Context, Deps, int32) error { return errors.New("x") }}
	d := testDeps(nil)
	s := fast(NewSupervisor([]Module{failing}, d))
	s.ParkFor = 20 * time.Millisecond
	stop := start(t, s)
	defer stop()
	eventually(t, "unparked by the timer", func() bool { return failing.runs.Load() > 5 })
}

func TestCriticalModuleIsNeverParked(t *testing.T) {
	failing := &fakeModule{name: "identity", critical: true, run: func(context.Context, Deps, int32) error {
		return errors.New("server down")
	}}
	d := testDeps(nil)
	stop := start(t, fast(NewSupervisor([]Module{failing}, d)))
	defer stop()
	eventually(t, "15 restarts", func() bool { return failing.runs.Load() >= 15 })
	if h, _ := d.Health.Get("identity"); h.State == string(StateParked) {
		t.Error("critical module parked")
	}
}

func TestRestartUnparksAndRestartsRunning(t *testing.T) {
	failing := &fakeModule{name: "deploy", run: func(context.Context, Deps, int32) error { return errors.New("x") }}
	var ticks atomic.Int64
	running := ticker("heartbeat", &ticks)
	d := testDeps(nil)
	s := fast(NewSupervisor([]Module{failing, running}, d))
	stop := start(t, s)
	defer stop()

	eventually(t, "parked", func() bool { h, _ := d.Health.Get("deploy"); return h.State == string(StateParked) })
	if err := s.Restart("deploy"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "unparked by Restart", func() bool { return failing.runs.Load() > 5 })

	eventually(t, "heartbeat running", func() bool { return ticks.Load() > 0 })
	if err := s.Restart("heartbeat"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "heartbeat restarted", func() bool { return running.runs.Load() == 2 })
	if h, _ := d.Health.Get("heartbeat"); h.Restarts != 0 {
		t.Errorf("a requested restart counted as a failure: %+v", h)
	}
	if err := s.Restart("nope"); !errors.Is(err, ErrUnknownModule) {
		t.Errorf("Restart(nope) = %v", err)
	}
}

func TestModuleReturningEarlyIsRestarted(t *testing.T) {
	quitter := &fakeModule{name: "network", critical: true, run: func(context.Context, Deps, int32) error { return nil }}
	d := testDeps(nil)
	stop := start(t, fast(NewSupervisor([]Module{quitter}, d)))
	defer stop()
	eventually(t, "restarted", func() bool { return quitter.runs.Load() >= 3 })
	h, _ := d.Health.Get("network")
	if h.LastError == nil || *h.LastError != errReturnedEarly.Error() {
		t.Errorf("health %+v", h)
	}
}

func TestDuplicateModuleNames(t *testing.T) {
	m := &fakeModule{name: "x", run: func(ctx context.Context, d Deps, n int32) error { <-ctx.Done(); return nil }}
	s := NewSupervisor([]Module{m, m}, testDeps(nil))
	if err := s.Run(context.Background()); err == nil {
		t.Error("want error for a duplicate name")
	}
}

func TestBreakerWindow(t *testing.T) {
	b := Breaker{Limit: 3, Window: time.Minute}
	t0 := time.Unix(0, 0)
	if b.Record(t0) || b.Record(t0.Add(10*time.Second)) {
		t.Fatal("tripped early")
	}
	if b.Record(t0.Add(2 * time.Minute)) {
		t.Error("old failures should have left the window")
	}
	if b.Record(t0.Add(2*time.Minute + time.Second)) {
		t.Error("tripped at the second failure in the window")
	}
	if !b.Record(t0.Add(2*time.Minute + 2*time.Second)) {
		t.Error("want trip at the third failure in the window")
	}
	b.Reset()
	if b.Record(t0.Add(3 * time.Minute)) {
		t.Error("tripped after Reset")
	}
}

func TestRestartDelay(t *testing.T) {
	r := restartDelay{min: time.Second, max: 5 * time.Second}
	var got []time.Duration
	for range 5 {
		got = append(got, r.take())
	}
	want := []time.Duration{1, 2, 4, 5, 5}
	for i := range want {
		if got[i] != want[i]*time.Second {
			t.Fatalf("delays %v", got)
		}
	}
	r.reset()
	if r.take() != time.Second {
		t.Error("reset")
	}
}

func TestRestartBetweenRunsIsNotLost(t *testing.T) {
	m := &fakeModule{name: "deploy", run: func(ctx context.Context, d Deps, n int32) error { <-ctx.Done(); return nil }}
	s := fast(NewSupervisor([]Module{m}, testDeps(nil)))
	r := &runner{mod: m, wake: make(chan struct{}, 1)}
	s.runners["deploy"] = r

	// Restart arrives while no run is active (after supervise drained wake).
	if err := s.Restart("deploy"); err != nil {
		t.Fatal(err)
	}
	r.mu.Lock()
	pending := r.pending
	r.mu.Unlock()
	if !pending {
		t.Fatal("request not recorded")
	}

	// The run that starts next fulfils it and clears the stale wake-up.
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); _ = s.runOnce(ctx, r, s.deps.Log) }()
	eventually(t, "run started", func() bool { return m.runs.Load() == 1 })
	r.mu.Lock()
	pending = r.pending
	r.mu.Unlock()
	if pending || len(r.wake) != 0 {
		t.Errorf("pending %v, wake %d after the run started", pending, len(r.wake))
	}
	cancel()
	<-done
}

// Feature modules wait for enrollment; core modules (Add) start at once, and
// the Enrolled signal releases the rest without a restart of the agent.
func TestFeatureModulesWaitForEnrollment(t *testing.T) {
	d := testDeps(nil)
	var feature, core atomic.Int64
	s := fast(NewSupervisor([]Module{ticker("feature", &feature)}, d))
	s.Add(ticker("core", &core))
	var enrolled atomic.Bool
	s.Enrolled = enrolled.Load
	defer start(t, s)()

	eventually(t, "core running", func() bool { return core.Load() > 0 })
	eventually(t, "feature waiting", func() bool { h, _ := d.Health.Get("feature"); return h.State == string(StateWaiting) })
	if feature.Load() != 0 {
		t.Fatal("feature module ran before enrollment")
	}
	if !d.Health.Alive(time.Minute) {
		t.Error("a waiting module must not make the agent unhealthy")
	}
	enrolled.Store(true)
	d.Signals.Publish(Enrolled{})
	eventually(t, "feature running", func() bool { return feature.Load() > 0 })
}

func TestEnrolledAtStartOpensTheGate(t *testing.T) {
	d := testDeps(nil)
	var feature atomic.Int64
	s := fast(NewSupervisor([]Module{ticker("feature", &feature)}, d))
	s.Enrolled = func() bool { return true }
	defer start(t, s)()
	eventually(t, "feature running", func() bool { return feature.Load() > 0 })
}
