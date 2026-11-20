package mqttfwd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// These tests need a real broker that allows anonymous clients, for example:
//
//	docker run --rm -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
//	MQTTFWD_TEST_BROKER_URL=mqtt://127.0.0.1:1883 go test -race -run Broker ./internal/mqttfwd/
//
// MQTTFWD_BURST_RATE and MQTTFWD_BURST_SECONDS size the burst (default 1000 msg/s for 5 s).
func brokerURL(t *testing.T) string {
	u := os.Getenv("MQTTFWD_TEST_BROKER_URL")
	if u == "" {
		t.Skip("MQTTFWD_TEST_BROKER_URL is not set")
	}
	return u
}

func envInt(key string, def int) int {
	if n, err := strconv.Atoi(os.Getenv(key)); err == nil && n > 0 {
		return n
	}
	return def
}

// controlPlane is a fake /internal/mqtt/ingest: it records payloads per topic
// and answers 503 to every 50th call, so the burst also goes through retries.
type controlPlane struct {
	mu    sync.Mutex
	got   map[string][]string
	calls atomic.Int64
}

func (c *controlPlane) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-MQTT-Internal-Token") != "tok" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	if c.calls.Add(1)%50 == 0 {
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	var body ingestBody
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		w.WriteHeader(http.StatusUnprocessableEntity)
		return
	}
	time.Sleep(2 * time.Millisecond) // a database write
	c.mu.Lock()
	c.got[body.Topic] = append(c.got[body.Topic], body.Payload)
	c.mu.Unlock()
	w.Write([]byte(`{"ok":true}`))
}

func (c *controlPlane) count(topic string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if topic != "" {
		return len(c.got[topic])
	}
	n := 0
	for _, v := range c.got {
		n += len(v)
	}
	return n
}

type forwarder struct {
	sess    *Session
	metrics *Metrics
	stop    func()
}

func startForwarder(t *testing.T, broker, cpURL string) *forwarder {
	t.Helper()
	cfg := Config{BrokerURL: broker, ClientID: fmt.Sprintf("fwd-test-%d", time.Now().UnixNano()), SharedGroup: "fwd-test",
		Workers: 16, QueueSize: 1024, BatchSize: 32, MaxAttempts: 10, SessionExpiry: time.Minute, DrainTimeout: 10 * time.Second}
	m := NewMetrics(prometheus.NewRegistry())
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	workCtx, stopWork := context.WithCancel(context.Background())
	pipe := NewPipeline(cfg.Workers, cfg.QueueSize, cfg.BatchSize, cfg.MaxAttempts,
		NewIngestClient(cpURL, "tok", cfg.Workers, 5*time.Second), m, log)
	pipe.backoff = func(int) time.Duration { return 10 * time.Millisecond }
	pipe.Start(workCtx)
	sess := NewSession(cfg, pipe, m, log)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- sess.Run(ctx, func(limit time.Duration) {
			deadline := time.Now().Add(limit)
			for !pipe.Idle() && time.Now().Before(deadline) {
				time.Sleep(10 * time.Millisecond)
			}
			stopWork()
			pipe.Wait()
		})
	}()
	waitFor(t, sess.Ready)
	return &forwarder{sess: sess, metrics: m, stop: func() {
		cancel()
		if err := <-done; err != nil {
			t.Error(err)
		}
	}}
}

func devicePublisher(t *testing.T, broker string) *autopaho.ConnectionManager {
	t.Helper()
	u, _ := url.Parse(broker)
	if u.Scheme == "mqtt" {
		u.Scheme = "tcp"
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	cm, err := autopaho.NewConnection(ctx, autopaho.ClientConfig{ServerUrls: []*url.URL{u}, KeepAlive: 30,
		CleanStartOnInitialConnection: true, ClientConfig: paho.ClientConfig{ClientID: fmt.Sprintf("dev-test-%d", time.Now().UnixNano())}})
	if err != nil {
		t.Fatal(err)
	}
	cctx, ccancel := context.WithTimeout(ctx, 10*time.Second)
	defer ccancel()
	if err := cm.AwaitConnection(cctx); err != nil {
		t.Fatal(err)
	}
	return cm
}

func TestBrokerBurstLosesNothing(t *testing.T) {
	broker := brokerURL(t)
	cp := &controlPlane{got: map[string][]string{}}
	srv := httptest.NewServer(cp)
	defer srv.Close()
	fwd := startForwarder(t, broker, srv.URL)
	defer fwd.stop()
	pub := devicePublisher(t, broker)

	rate, seconds, devices := envInt("MQTTFWD_BURST_RATE", 1000), envInt("MQTTFWD_BURST_SECONDS", 5), 100
	total := rate * seconds
	// Publish in 10 ms ticks; QoS 1 publishes run in parallel so the device side keeps the rate.
	var wg sync.WaitGroup
	var failed atomic.Int64
	start := time.Now()
	tick := time.NewTicker(10 * time.Millisecond)
	perTick := rate / 100
	sent := 0
	for sent < total {
		<-tick.C
		for i := 0; i < perTick && sent < total; i, sent = i+1, sent+1 {
			n := sent
			wg.Add(1)
			go func() {
				defer wg.Done()
				topic := fmt.Sprintf("devices/dev%03d/metrics", n%devices)
				pctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
				defer cancel()
				if _, err := pub.Publish(pctx, &paho.Publish{Topic: topic, QoS: 1, Payload: []byte(strconv.Itoa(n))}); err != nil {
					failed.Add(1)
				}
			}()
		}
	}
	tick.Stop()
	wg.Wait()
	pubDur := time.Since(start)
	if failed.Load() > 0 {
		t.Fatalf("%d publishes failed on the device side", failed.Load())
	}
	deadline := time.Now().Add(60 * time.Second)
	for cp.count("") < total && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	got := cp.count("")
	t.Logf("published %d in %v (%.0f msg/s); forwarded %d in %v; ingest calls %d; retries %.0f",
		total, pubDur.Round(time.Millisecond), float64(total)/pubDur.Seconds(), got, time.Since(start).Round(time.Millisecond),
		cp.calls.Load(), testutil.ToFloat64(fwd.metrics.Retries))
	if got < total {
		t.Fatalf("lost %d of %d messages", total-got, total)
	}
	// Exactly once on a healthy connection, and in order per device. (MQTT QoS 1 may
	// redeliver after a reconnect; that would show up here as more than total.)
	if got != total {
		t.Fatalf("forwarded %d, want %d", got, total)
	}
	cp.mu.Lock()
	for topic, payloads := range cp.got {
		last := -1
		for _, p := range payloads {
			n, _ := strconv.Atoi(p)
			if n <= last {
				t.Fatalf("%s: %d after %d; per-device order lost", topic, n, last)
			}
			last = n
		}
	}
	cp.mu.Unlock()
	if v := testutil.ToFloat64(fwd.metrics.MessagesReceived.WithLabelValues("metrics")); v != float64(total) {
		t.Fatalf("messages_received_total{kind=metrics} = %v", v)
	}
	if v := testutil.ToFloat64(fwd.metrics.Dropped.WithLabelValues("queue_full")); v != 0 {
		t.Fatalf("dropped %v", v)
	}
}

func TestBrokerWatchAndPublish(t *testing.T) {
	broker := brokerURL(t)
	cp := &controlPlane{got: map[string][]string{}}
	srv := httptest.NewServer(cp)
	defer srv.Close()
	fwd := startForwarder(t, broker, srv.URL)
	defer fwd.stop()
	ctx := context.Background()

	// A watched non-device topic is forwarded too, until the last unwatch.
	if err := fwd.sess.Watch(ctx, "lab/temp"); err != nil {
		t.Fatal(err)
	}
	fwd.sess.Watch(ctx, "lab/temp")
	if err := fwd.sess.Publish(ctx, "lab/temp", "1", 1, false); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return cp.count("lab/temp") == 1 })
	fwd.sess.Unwatch(ctx, "lab/temp") // one reference left
	fwd.sess.Publish(ctx, "lab/temp", "2", 1, false)
	waitFor(t, func() bool { return cp.count("lab/temp") == 2 })
	fwd.sess.Unwatch(ctx, "lab/temp")
	fwd.sess.Publish(ctx, "lab/temp", "3", 1, false)
	fwd.sess.Publish(ctx, "devices/x/status", `{"online":true}`, 1, false) // marker on a device topic
	waitFor(t, func() bool { return cp.count("devices/x/status") == 1 })
	if n := cp.count("lab/temp"); n != 2 {
		t.Fatalf("lab/temp forwarded %d times after the last unwatch, want 2", n)
	}
	if v := testutil.ToFloat64(fwd.metrics.MQTTConnected); v != 1 {
		t.Fatalf("mqtt_connected = %v", v)
	}
}
