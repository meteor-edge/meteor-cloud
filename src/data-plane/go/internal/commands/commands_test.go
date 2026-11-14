package commands

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

type fixture struct {
	svc   *Service
	bus   *bus.Memory
	clock time.Time
	mu    sync.Mutex
	sent  []string
	tgt   *identity.Identity
}

func newFixture(t *testing.T) *fixture {
	f := &fixture{clock: time.Date(2026, 11, 11, 12, 0, 0, 0, time.UTC), bus: bus.NewMemory()}
	policy := identity.NewStore(0)
	policy.Apply(&identity.Snapshot{Version: "1", Applications: []identity.SnapshotApplication{
		{ID: "app-detector", OrganizationID: "org", Slug: "detector", Permissions: identity.Permissions{ReceiveCommands: true}},
		{ID: "app-deaf", OrganizationID: "org", Slug: "deaf"},
		{ID: "app-other", OrganizationID: "other", Slug: "detector", Permissions: identity.Permissions{ReceiveCommands: true}},
	}})
	f.bus.Start(context.Background(), bus.Handlers{OnCommand: func(c *storage.Command) {
		f.mu.Lock()
		f.sent = append(f.sent, c.ID)
		f.mu.Unlock()
	}})
	f.svc = NewService(Config{AckTimeout: 5 * time.Second, MaxBackoff: 20 * time.Second}, storage.NewMemory(), f.bus,
		policy, observability.NewMetrics(), slog.New(slog.NewTextHandler(io.Discard, nil)))
	f.svc.SetClock(func() time.Time { return f.clock })
	f.tgt = &identity.Identity{OrganizationID: "org", ApplicationID: "app-detector", Application: "detector",
		DeviceID: "d1", Permissions: identity.Permissions{ReceiveCommands: true}}
	return f
}

func (f *fixture) create(t *testing.T, req CreateRequest) *storage.Command {
	if req.OrganizationID == "" {
		req.OrganizationID = "org"
	}
	if req.Application == "" && req.ApplicationID == "" {
		req.Application = "detector"
	}
	if req.Name == "" {
		req.Name = "restart"
	}
	if req.IssuedBy == "" {
		req.IssuedBy = "user:1"
	}
	cmd, err := f.svc.Create(context.Background(), req)
	if err != nil {
		t.Fatal(err)
	}
	return cmd
}

func (f *fixture) sentCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.sent)
}

func TestHappyPathStateMachine(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	cmd := f.create(t, CreateRequest{Payload: json.RawMessage(`{"delay":2}`), DeviceID: "d1"})
	if cmd.State != storage.StateDispatched || cmd.Attempts != 1 || f.sentCount() != 1 {
		t.Fatalf("after create: %+v", cmd)
	}
	for _, step := range []struct{ status, state string }{
		{AckReceived, storage.StateReceived}, {AckExecuting, storage.StateExecuting}, {AckSucceeded, storage.StateSucceeded},
	} {
		got, err := f.svc.Ack(ctx, f.tgt, cmd.ID, step.status, json.RawMessage(`{"ok":true}`), "")
		if err != nil || got.State != step.state {
			t.Fatalf("%s: %+v %v", step.status, got, err)
		}
	}
	final, _ := f.svc.Get(ctx, "org", cmd.ID)
	if final.CompletedAt == nil || final.ReceivedAt == nil || string(final.Result) != `{"ok":true}` {
		t.Fatalf("final: %+v", final)
	}
	// Late, repeated and out-of-order acknowledgements never move it backwards.
	for _, status := range []string{AckReceived, AckExecuting, AckFailed, AckSucceeded} {
		got, err := f.svc.Ack(ctx, f.tgt, cmd.ID, status, nil, "late")
		if err != nil || got.State != storage.StateSucceeded || got.Error != "" {
			t.Fatalf("late %s changed a terminal command: %+v %v", status, got, err)
		}
	}
}

func TestSkippingStraightToResultIsAllowed(t *testing.T) {
	f := newFixture(t)
	cmd := f.create(t, CreateRequest{})
	got, err := f.svc.Ack(context.Background(), f.tgt, cmd.ID, AckFailed, nil, "disk full")
	if err != nil || got.State != storage.StateFailed || got.Error != "disk full" {
		t.Fatalf("%+v %v", got, err)
	}
}

func TestOnlyTheTargetMayAcknowledge(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	cmd := f.create(t, CreateRequest{DeviceID: "d1"})
	wrong := []*identity.Identity{
		{OrganizationID: "org", ApplicationID: "app-detector", DeviceID: "d2", Permissions: identity.Permissions{ReceiveCommands: true}},
		{OrganizationID: "org", ApplicationID: "app-deaf", DeviceID: "d1", Permissions: identity.Permissions{ReceiveCommands: true}},
		{OrganizationID: "org", ApplicationID: "app-detector", DeviceID: "d1"},
	}
	for i, id := range wrong {
		if _, err := f.svc.Ack(ctx, id, cmd.ID, AckSucceeded, nil, ""); !errors.Is(err, ErrForbidden) {
			t.Errorf("identity %d: want ErrForbidden, got %v", i, err)
		}
	}
	other := &identity.Identity{OrganizationID: "other", ApplicationID: "app-other", DeviceID: "d1",
		Permissions: identity.Permissions{ReceiveCommands: true}}
	if _, err := f.svc.Ack(ctx, other, cmd.ID, AckSucceeded, nil, ""); !errors.Is(err, storage.ErrNotFound) {
		t.Errorf("another organization must not even see the command: %v", err)
	}
	if got, _ := f.svc.Get(ctx, "org", cmd.ID); got.State != storage.StateDispatched {
		t.Fatalf("state changed by an unauthorized ack: %s", got.State)
	}
	issuer := &identity.Identity{OrganizationID: "org", ApplicationID: "app-x"}
	cmd2 := f.create(t, CreateRequest{IssuedBy: "app:app-x"})
	if !CanSee(issuer, cmd2) || CanSee(issuer, cmd) || !CanSee(f.tgt, cmd) || CanSee(other, cmd) {
		t.Fatal("visibility: issuer and target only")
	}
}

func TestValidation(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	bad := []CreateRequest{
		{OrganizationID: "org", Application: "ghost", Name: "x"},
		{OrganizationID: "org", Application: "deaf", Name: "x"},        // no receive_commands
		{OrganizationID: "org", ApplicationID: "app-other", Name: "x"}, // other organization's application
		{OrganizationID: "org", Application: "detector"},               // no name
		{OrganizationID: "org", Application: "detector", Name: "x", Payload: json.RawMessage(`[1]`)},
		{OrganizationID: "org", Application: "detector", Name: "x", TimeoutSeconds: 10_000_000},
	}
	for i, req := range bad {
		var invalid *InvalidError
		if _, err := f.svc.Create(ctx, req); !errors.As(err, &invalid) {
			t.Errorf("case %d: want InvalidError, got %v", i, err)
		}
	}
	if f.sentCount() != 0 {
		t.Fatal("an invalid command was dispatched")
	}
}

func TestIdempotencyKeyCreatesOnce(t *testing.T) {
	f := newFixture(t)
	a := f.create(t, CreateRequest{IdempotencyKey: "deploy-42"})
	b := f.create(t, CreateRequest{IdempotencyKey: "deploy-42"})
	if a.ID != b.ID || f.sentCount() != 1 {
		t.Fatalf("second create must return the first command without dispatching again")
	}
}

func TestRedeliveryWithBackoffUntilReceived(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	cmd := f.create(t, CreateRequest{ExpiresInSeconds: 600})
	f.svc.Sweep(ctx)
	if f.sentCount() != 1 {
		t.Fatal("redelivered before the acknowledgement timeout")
	}
	f.clock = f.clock.Add(6 * time.Second) // > 5s ack timeout
	f.svc.Sweep(ctx)
	if f.sentCount() != 2 {
		t.Fatalf("want a redelivery, sent=%d", f.sentCount())
	}
	f.clock = f.clock.Add(6 * time.Second) // backoff is now 10s
	f.svc.Sweep(ctx)
	if f.sentCount() != 2 {
		t.Fatal("backoff not applied")
	}
	f.clock = f.clock.Add(5 * time.Second)
	f.svc.Sweep(ctx)
	if f.sentCount() != 3 {
		t.Fatalf("want third delivery, sent=%d", f.sentCount())
	}
	if _, err := f.svc.Ack(ctx, f.tgt, cmd.ID, AckReceived, nil, ""); err != nil {
		t.Fatal(err)
	}
	f.clock = f.clock.Add(30 * time.Second)
	f.svc.Sweep(ctx)
	if f.sentCount() != 3 {
		t.Fatal("a received command must not be redelivered")
	}
}

func TestTimeouts(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()

	// Never acknowledged: expires.
	expiring := f.create(t, CreateRequest{ExpiresInSeconds: 30})
	// Received but no result within timeout_seconds.
	hanging := f.create(t, CreateRequest{TimeoutSeconds: 10})
	f.svc.Ack(ctx, f.tgt, hanging.ID, AckExecuting, nil, "")
	// Bounded attempts.
	bounded := f.create(t, CreateRequest{MaxAttempts: 1})
	// Completed in time: untouched.
	fine := f.create(t, CreateRequest{})
	f.svc.Ack(ctx, f.tgt, fine.ID, AckSucceeded, nil, "")

	f.clock = f.clock.Add(31 * time.Second)
	f.svc.Sweep(ctx)

	for id, want := range map[string]string{expiring.ID: storage.StateTimeout, hanging.ID: storage.StateTimeout,
		bounded.ID: storage.StateTimeout, fine.ID: storage.StateSucceeded} {
		got, _ := f.svc.Get(ctx, "org", id)
		if got.State != want {
			t.Errorf("command %s: state %s, want %s (%s)", id, got.State, want, got.Error)
		}
		if want == storage.StateTimeout && (got.Error == "" || got.CompletedAt == nil) {
			t.Errorf("timeout must say why: %+v", got)
		}
	}
	// A result arriving after the timeout does not resurrect the command.
	late, _ := f.svc.Ack(ctx, f.tgt, hanging.ID, AckSucceeded, nil, "")
	if late.State != storage.StateTimeout {
		t.Fatal("late result changed a timed-out command")
	}
}

func TestTransportFailureKeepsCommandPendingAndRetries(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.bus.Fail = errors.New("broker down")
	cmd := f.create(t, CreateRequest{})
	if cmd.State != storage.StatePending {
		t.Fatalf("not handed to the transport, so still PENDING: %s", cmd.State)
	}
	f.bus.Fail = nil
	f.clock = f.clock.Add(6 * time.Second)
	f.svc.Sweep(ctx)
	got, _ := f.svc.Get(ctx, "org", cmd.ID)
	if got.State != storage.StateDispatched || f.sentCount() != 1 {
		t.Fatalf("retry after recovery: %+v sent=%d", got, f.sentCount())
	}
}

func TestUndeliveredForReconnectingTarget(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	mine := f.create(t, CreateRequest{DeviceID: "d1"})
	any := f.create(t, CreateRequest{})
	f.create(t, CreateRequest{DeviceID: "d2"})
	done := f.create(t, CreateRequest{DeviceID: "d1"})
	f.svc.Ack(ctx, f.tgt, done.ID, AckReceived, nil, "")

	got, err := f.svc.Undelivered(ctx, f.tgt)
	if err != nil || len(got) != 2 {
		t.Fatalf("want 2 undelivered, got %d (%v)", len(got), err)
	}
	ids := map[string]bool{got[0].ID: true, got[1].ID: true}
	if !ids[mine.ID] || !ids[any.ID] {
		t.Fatalf("wrong commands: %v", ids)
	}
	deaf := &identity.Identity{OrganizationID: "org", ApplicationID: "app-detector", DeviceID: "d1"}
	if got, _ := f.svc.Undelivered(ctx, deaf); len(got) != 0 {
		t.Fatal("no receive_commands permission, no commands")
	}
}
