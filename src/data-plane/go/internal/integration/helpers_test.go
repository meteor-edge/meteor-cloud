// Package integration tests the device event path against a real NATS
// JetStream server (embedded nats-server): ingestion, the stream, and the rule
// processor and ClickHouse writer consumers.
package integration

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"

	"meteorcloud/dataplane/internal/chwriter"
	"meteorcloud/dataplane/internal/deviceingest"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/ruleproc"
)

const (
	devA = "11111111-1111-4111-8111-111111111111"
	devB = "22222222-2222-4222-8222-222222222222"
	devX = "33333333-3333-4333-8333-333333333333" // not enrolled
	orgA = "aaaaaaaa-0000-4000-8000-00000000000a"
	orgB = "bbbbbbbb-0000-4000-8000-00000000000b"
)

var quiet = slog.New(slog.NewTextHandler(io.Discard, nil))

type dir map[string]string

func (d dir) Organization(id string) (string, bool, error) {
	org, ok := d[id]
	return org, ok, nil
}

var devices = dir{devA: orgA, devB: orgB}

func streamConfig() natsjs.StreamConfig {
	return natsjs.StreamConfig{Name: natsjs.DefaultStream, Subjects: natsjs.DefaultSubjects, Retention: jetstream.LimitsPolicy,
		MaxBytes: -1, MaxMsgSize: -1, Storage: jetstream.FileStorage, Replicas: 1, DuplicateWindow: 2 * time.Minute,
		Discard: jetstream.DiscardOld, Manage: true}
}

func consumerConfig(durable string) natsjs.ConsumerConfig {
	return natsjs.ConsumerConfig{Stream: natsjs.DefaultStream, Durable: durable, FilterSubjects: natsjs.DefaultSubjects,
		AckWait: 5 * time.Second, MaxDeliver: -1, MaxAckPending: 1000, FetchBatch: 50, FetchMaxWait: 100 * time.Millisecond}
}

func ensureStream(t *testing.T, js jetstream.JetStream, cfg natsjs.StreamConfig) jetstream.Stream {
	t.Helper()
	s, err := natsjs.EnsureStream(context.Background(), js, cfg)
	if err != nil {
		t.Fatalf("stream: %v", err)
	}
	return s
}

func ensureConsumer(t *testing.T, js jetstream.JetStream, durable string) jetstream.Consumer {
	t.Helper()
	c, err := natsjs.EnsureConsumer(context.Background(), js, consumerConfig(durable))
	if err != nil {
		t.Fatalf("consumer: %v", err)
	}
	return c
}

func newIngester(js jetstream.JetStream, nm *natsjs.Metrics, reg prometheus.Registerer) (*deviceingest.Ingester, *deviceingest.Metrics) {
	im := deviceingest.NewMetrics(reg)
	return &deviceingest.Ingester{Dir: devices, Limits: envelope.DefaultLimits(), Metrics: im, Log: quiet, MaxBackoff: 200 * time.Millisecond,
		Publisher: &natsjs.Publisher{JS: js, Stream: natsjs.DefaultStream, Metrics: nm}}, im
}

func ingest(t *testing.T, in *deviceingest.Ingester, device, channel, payload string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if !in.Handle(ctx, "devices/"+device+"/"+channel, []byte(payload)) {
		t.Fatalf("message on %s/%s was not handled", device, channel)
	}
}

// recorder is a rule handler that remembers what it saw and can fail on demand.
type recorder struct {
	mu     sync.Mutex
	events []*envelope.Event
	calls  map[string]int
	failN  int // fail the first failN calls for each event id
}

func (r *recorder) Handle(_ context.Context, ev *envelope.Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.calls == nil {
		r.calls = map[string]int{}
	}
	r.calls[ev.ID]++
	if r.calls[ev.ID] <= r.failN {
		return errors.New("temporary failure")
	}
	r.events = append(r.events, ev)
	return nil
}

func (r *recorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.events)
}

// fakeClickHouse is an idempotent store that can be taken down.
type fakeClickHouse struct {
	mu       sync.Mutex
	rows     map[string]*envelope.Event
	down     atomic.Bool
	failures atomic.Int64
}

func (f *fakeClickHouse) InsertEvents(_ context.Context, events []*envelope.Event) ([]bool, error) {
	if f.down.Load() {
		f.failures.Add(1)
		return nil, errors.New("clickhouse: connection refused")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.rows == nil {
		f.rows = map[string]*envelope.Event{}
	}
	out := make([]bool, len(events))
	for i, ev := range events {
		key := ev.OrganizationID + "/" + ev.ID
		if _, ok := f.rows[key]; !ok {
			f.rows[key] = ev
			out[i] = true
		}
	}
	return out, nil
}

func (f *fakeClickHouse) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.rows)
}

func runProcessor(t *testing.T, cons jetstream.Consumer, h ruleproc.Handler, nm *natsjs.Metrics) (stop func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	p := &ruleproc.Processor{Cfg: ruleproc.Config{Name: "rule-processor", Workers: 4, FetchBatch: 50, FetchMaxWait: 100 * time.Millisecond,
		RetryBackoff: []time.Duration{50 * time.Millisecond}}, Cons: cons, Handler: h, Metrics: nm, Log: quiet}
	done := make(chan struct{})
	go func() { p.Run(ctx); close(done) }()
	return func() { cancel(); <-done }
}

func runWriter(t *testing.T, cons jetstream.Consumer, store chwriter.Inserter, nm *natsjs.Metrics, reg prometheus.Registerer, cfg chwriter.Config) (stop func(), m *chwriter.Metrics) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	m = chwriter.NewMetrics(reg)
	cfg.Name = "clickhouse-writer"
	w := &chwriter.Writer{Cfg: cfg, Cons: cons, Store: store, NATS: nm, Metrics: m, Log: quiet}
	done := make(chan struct{})
	go func() { w.Run(ctx); close(done) }()
	return func() { cancel(); <-done }, m
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func info(t *testing.T, cons jetstream.Consumer) *jetstream.ConsumerInfo {
	t.Helper()
	i, err := cons.Info(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return i
}

func counter(c prometheus.Collector) float64 { return testutil.ToFloat64(c) }

func jetstreamConsumer(durable, filter string) jetstream.ConsumerConfig {
	return jetstream.ConsumerConfig{Durable: durable, FilterSubject: filter, AckPolicy: jetstream.AckExplicitPolicy}
}
