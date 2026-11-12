package ingest

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

func ev(id string) *envelope.Event {
	return &envelope.Event{ID: id, SchemaVersion: 1, Kind: "telemetry", Type: "s", Stream: "energy.power",
		OrganizationID: "org", Time: time.Now(), ReceivedAt: time.Now(), Data: json.RawMessage(`{"p":1}`)}
}

func setup(t *testing.T, cfg Config) (*Pipeline, *storage.Memory, *bus.Memory, func() string) {
	store, b := storage.NewMemory(), bus.NewMemory()
	var mu sync.Mutex
	live := []string{}
	b.Start(context.Background(), bus.Handlers{OnLive: func(e *envelope.Event) {
		mu.Lock()
		live = append(live, e.ID)
		mu.Unlock()
	}})
	p := New(cfg, store, b, observability.NewMetrics(), slog.New(slog.NewTextHandler(io.Discard, nil)))
	p.Start()
	t.Cleanup(func() { p.Stop(context.Background()) })
	// Distribution follows storage: give it a moment before reading.
	return p, store, b, func() string {
		time.Sleep(50 * time.Millisecond)
		mu.Lock()
		defer mu.Unlock()
		return fmt.Sprint(live)
	}
}

func TestStoredThenDistributedAndDuplicatesAreNotResent(t *testing.T) {
	p, _, _, live := setup(t, Config{})
	st, err := p.Submit(context.Background(), []*envelope.Event{ev("a"), ev("b"), ev("a")})
	if err != nil || fmt.Sprint(st) != "[stored stored duplicate]" {
		t.Fatalf("%v %v", st, err)
	}
	st, err = p.Submit(context.Background(), []*envelope.Event{ev("a")})
	if err != nil || st[0] != StatusDuplicate {
		t.Fatalf("retry must be reported as duplicate: %v %v", st, err)
	}
	if got := live(); got != "[a b]" {
		t.Fatalf("live distribution wrong: %v", got)
	}
}

func TestTransientStorageFailureIsRetried(t *testing.T) {
	p, store, _, _ := setup(t, Config{WriteRetries: 3, RetryBackoff: time.Millisecond})
	store.FailInserts = 2
	st, err := p.Submit(context.Background(), []*envelope.Event{ev("a")})
	if err != nil || st[0] != StatusStored {
		t.Fatalf("%v %v", st, err)
	}
}

func TestPersistentStorageFailureIsReportedNotDropped(t *testing.T) {
	p, store, _, live := setup(t, Config{WriteRetries: 1, RetryBackoff: time.Millisecond})
	store.FailInserts = 10
	_, err := p.Submit(context.Background(), []*envelope.Event{ev("a")})
	if err == nil {
		t.Fatal("the caller must learn that nothing was stored")
	}
	if live() != "[]" {
		t.Fatal("an event that was not stored must not be distributed")
	}
	if p.Depth() != 0 {
		t.Fatal("queue accounting leaked")
	}
	// The same event can be retried later.
	store.FailInserts = 0
	st, err := p.Submit(context.Background(), []*envelope.Event{ev("a")})
	if err != nil || st[0] != StatusStored {
		t.Fatalf("retry after recovery: %v %v", st, err)
	}
}

type slowStore struct {
	*storage.Memory
	gate chan struct{}
}

func (s *slowStore) InsertEvents(ctx context.Context, e []*envelope.Event) ([]bool, error) {
	<-s.gate
	return s.Memory.InsertEvents(ctx, e)
}

func TestBackpressureWhenQueueIsFull(t *testing.T) {
	store := &slowStore{Memory: storage.NewMemory(), gate: make(chan struct{})}
	p := New(Config{QueueSize: 4, Workers: 1, MaxBatch: 2}, store, bus.NewMemory(), observability.NewMetrics(),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	p.Start()
	var wg sync.WaitGroup
	results := make([]error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, results[i] = p.Submit(context.Background(), []*envelope.Event{ev(fmt.Sprintf("e%d", i))})
		}(i)
	}
	deadline := time.Now().Add(2 * time.Second)
	for p.Depth() < 4 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if _, err := p.Submit(context.Background(), []*envelope.Event{ev("overflow")}); !errors.Is(err, ErrOverloaded) {
		t.Fatalf("want ErrOverloaded, got %v", err)
	}
	close(store.gate)
	wg.Wait()
	for i, err := range results {
		if err != nil {
			t.Fatalf("accepted request %d failed: %v", i, err)
		}
	}
	if p.Depth() != 0 {
		t.Fatalf("depth=%d after drain", p.Depth())
	}
	p.Stop(context.Background())
	if _, err := p.Submit(context.Background(), []*envelope.Event{ev("late")}); !errors.Is(err, ErrStopped) {
		t.Fatalf("want ErrStopped, got %v", err)
	}
}

func TestLiveFailureDoesNotFailTheWrite(t *testing.T) {
	p, _, _, _ := setup(t, Config{})
	p.bus = &bus.Memory{Fail: errors.New("broker down")}
	st, err := p.Submit(context.Background(), []*envelope.Event{ev("a")})
	if err != nil || st[0] != StatusStored {
		t.Fatalf("storage succeeded, so the write succeeded: %v %v", st, err)
	}
}
