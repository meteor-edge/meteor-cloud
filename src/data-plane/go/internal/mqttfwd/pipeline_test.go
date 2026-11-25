package mqttfwd

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// fakeForwarder records what was forwarded and fails on demand.
type fakeForwarder struct {
	mu    sync.Mutex
	got   map[string][]string // device -> payloads in order
	fails map[string]int      // payload -> failures left before success
	errs  map[string]error    // payload -> error to return while failing
	delay time.Duration
	calls atomic.Int64
}

func newFake() *fakeForwarder {
	return &fakeForwarder{got: map[string][]string{}, fails: map[string]int{}, errs: map[string]error{}}
}

func (f *fakeForwarder) Forward(ctx context.Context, topic string, payload []byte) error {
	f.calls.Add(1)
	if f.delay > 0 {
		select {
		case <-time.After(f.delay):
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	p := string(payload)
	if f.fails[p] > 0 {
		f.fails[p]--
		return f.errs[p]
	}
	f.got[topic] = append(f.got[topic], p)
	return nil
}

func (f *fakeForwarder) total() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, v := range f.got {
		n += len(v)
	}
	return n
}

func testPipeline(t *testing.T, fwd Forwarder, workers, queue int) (*Pipeline, *Metrics) {
	t.Helper()
	m := NewMetrics(prometheus.NewRegistry())
	p := NewPipeline(workers, queue, 8, 3, fwd, m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	p.backoff = func(int) time.Duration { return time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)
	t.Cleanup(func() { cancel(); p.Wait() })
	return p, m
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("timed out")
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func TestPipelineKeepsPerDeviceOrderAndAcksEverything(t *testing.T) {
	fwd := newFake()
	p, m := testPipeline(t, fwd, 4, 64)
	var acked atomic.Int64
	const devices, each = 20, 50
	for i := range each {
		for d := range devices {
			p.Offer(Message{Topic: fmt.Sprintf("devices/d%d/status", d), Payload: []byte(fmt.Sprint(i)),
				Received: time.Now(), Ack: func() { acked.Add(1) }}, true)
		}
	}
	waitFor(t, func() bool { return acked.Load() == devices*each })
	for d := range devices {
		got := fwd.got[fmt.Sprintf("devices/d%d/status", d)]
		for i, v := range got {
			if v != fmt.Sprint(i) {
				t.Fatalf("device %d: message %d is %s; order lost", d, i, v)
			}
		}
	}
	if v := testutil.ToFloat64(m.IngestForward.WithLabelValues("ok")); v != devices*each {
		t.Fatalf("ok forwards = %v", v)
	}
	waitFor(t, p.Idle)
}

func TestPipelineRetriesThenAcks(t *testing.T) {
	fwd := newFake()
	fwd.fails["a"], fwd.errs["a"] = 5, fmt.Errorf("connection refused") // retried without limit
	fwd.fails["b"], fwd.errs["b"] = 2, &StatusError{Status: 500}        // within MaxAttempts=3
	p, m := testPipeline(t, fwd, 1, 8)
	var acked atomic.Int64
	for _, payload := range []string{"a", "b"} {
		p.Offer(Message{Topic: "devices/x/metrics", Payload: []byte(payload), Ack: func() { acked.Add(1) }}, true)
	}
	waitFor(t, func() bool { return acked.Load() == 2 })
	if got := fwd.got["devices/x/metrics"]; len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Fatalf("forwarded %v", got)
	}
	if v := testutil.ToFloat64(m.Retries); v != 7 {
		t.Fatalf("retries = %v, want 7", v)
	}
	if v := testutil.ToFloat64(m.IngestForward.WithLabelValues("unreachable")); v != 5 {
		t.Fatalf("unreachable = %v", v)
	}
	if v := testutil.ToFloat64(m.IngestForward.WithLabelValues("http_error")); v != 2 {
		t.Fatalf("http_error = %v", v)
	}
}

func TestPipelineDropsWhatCanNeverSucceed(t *testing.T) {
	fwd := newFake()
	fwd.fails["bad"], fwd.errs["bad"] = 1, &StatusError{Status: 422}
	fwd.fails["poison"], fwd.errs["poison"] = 100, &StatusError{Status: 500}
	p, m := testPipeline(t, fwd, 1, 8)
	var acked atomic.Int64
	for _, payload := range []string{"bad", "poison", "good"} {
		p.Offer(Message{Topic: "devices/x/status", Payload: []byte(payload), Ack: func() { acked.Add(1) }}, true)
	}
	waitFor(t, func() bool { return acked.Load() == 3 })
	if got := fwd.got["devices/x/status"]; len(got) != 1 || got[0] != "good" {
		t.Fatalf("forwarded %v", got)
	}
	if v := testutil.ToFloat64(m.Dropped.WithLabelValues("rejected")); v != 1 {
		t.Fatalf("rejected = %v", v)
	}
	if v := testutil.ToFloat64(m.Dropped.WithLabelValues("retries_exhausted")); v != 1 {
		t.Fatalf("retries_exhausted = %v", v)
	}
}

func TestPipelineQoS0DropsWhenFull(t *testing.T) {
	fwd := newFake()
	fwd.delay = time.Hour // the worker takes one message and holds it
	m := NewMetrics(prometheus.NewRegistry())
	p := NewPipeline(1, 2, 1, 3, fwd, m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)
	p.Offer(Message{Topic: "devices/x/metrics"}, false)
	waitFor(t, func() bool { return fwd.calls.Load() == 1 })
	accepted := 0
	for range 5 {
		if p.Offer(Message{Topic: "devices/x/metrics"}, false) {
			accepted++
		}
	}
	if accepted != 2 {
		t.Fatalf("accepted %d, want the queue size 2", accepted)
	}
	if v := testutil.ToFloat64(m.Dropped.WithLabelValues("queue_full")); v != 3 {
		t.Fatalf("queue_full = %v", v)
	}
	// Stopping leaves the held message unacknowledged and unblocks waiting offers.
	cancel()
	p.Wait()
	if p.Offer(Message{Topic: "devices/x/metrics"}, true) {
		t.Fatal("blocking offer after stop must fail, not hang")
	}
}

func TestShardOfIsPerDevice(t *testing.T) {
	a := shardOf("devices/abc/status", 16)
	for _, topic := range []string{"devices/abc/metrics", "devices/abc/commands/result", "devices/abc/events"} {
		if shardOf(topic, 16) != a {
			t.Fatalf("%s went to another shard than devices/abc/status", topic)
		}
	}
	seen := map[int]bool{}
	for i := range 200 {
		seen[shardOf(fmt.Sprintf("devices/%d/status", i), 16)] = true
	}
	if len(seen) < 12 {
		t.Fatalf("200 devices used only %d of 16 shards", len(seen))
	}
}
