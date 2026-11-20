package metrics

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/fakebroker"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
)

// fakeHelper plays the simulated device of the contract suite: the first
// docker ps lists nothing, later ones list demo-app.
type fakeHelper struct {
	looks    int
	cursors  []string
	journal  []helper.Warning
	noDocker bool
}

func (f *fakeHelper) Call(_ context.Context, op string, params, out any) error {
	reply := func(v any) error {
		b, _ := json.Marshal(v)
		return json.Unmarshal(b, out)
	}
	switch op {
	case "docker.ps":
		if f.noDocker {
			return errors.New("docker not found")
		}
		f.looks++
		if f.looks == 1 {
			return reply(helper.DockerOutput{Output: "\n", OK: true})
		}
		return reply(helper.DockerOutput{Output: "demo-app\n", OK: true})
	case "docker.inspect":
		return reply(helper.DockerOutput{Output: "/demo-app|running|true|0|false|0|2026-01-01T00:00:00Z|\n", OK: true})
	case "docker.stats":
		return reply(helper.DockerOutput{Output: "demo-app|1.50%|10MiB / 100MiB\n", OK: true})
	case "logs.warnings":
		f.cursors = append(f.cursors, params.(helper.WarningsRequest).AfterCursor)
		entries := f.journal
		f.journal = nil
		return reply(helper.WarningsReply{Entries: entries})
	}
	return errors.New("unexpected op " + op)
}

func str(s string) *string { return &s }

func TestObserverRounds(t *testing.T) {
	files := map[string]string{
		"/proc/net/dev":      "h\nh\n eth0: 1000 1 0 0 0 0 0 0 500 1 0 0 0 0 0 0\n",
		"/proc/net/wireless": "h\nh\n wlan0: 0000 54. -61. -256 0 0\n",
	}
	d := agenttest.Deps(t, fakecp.New(t), nil, files)
	f := &fakeHelper{journal: []helper.Warning{{Cursor: "c1", Message: "Under-voltage detected", Priority: new(int), Unit: str("kernel"), Timestamp: str("2026-01-01T00:00:00.000000Z")}}}
	o := newObserver(d)
	o.helper, o.apps = f, newApps(f)
	clock := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	o.now = func() time.Time { return clock }
	d.Log.Error("one error") // counted by agent_errors

	body, events := o.collect(context.Background())
	if body["agent_errors"] != int64(1) || body["wifi_signal_dbm"] != -61.0 || body["timestamp"] != "2026-01-01T00:00:00Z" {
		t.Errorf("first body = %v", body)
	}
	if _, ok := body["net_rx_bps"]; ok {
		t.Error("no network rate on the first look")
	}
	for k := range body {
		if strings.HasPrefix(k, "app.") {
			t.Errorf("first look has app key %s", k)
		}
	}
	if len(events) != 1 || events[0]["type"] != "log" || events[0]["message"] != "Under-voltage detected" || events[0]["timestamp"] != "2026-01-01T00:00:00.000000Z" {
		t.Errorf("first events = %v", events)
	}

	clock = clock.Add(10 * time.Second)
	writeFile(t, d.Host.Root, "/proc/net/dev", "h\nh\n eth0: 2000 1 0 0 0 0 0 0 600 1 0 0 0 0 0 0\n")
	body, events = o.collect(context.Background())
	if body["net_rx_bps"] != 100.0 || body["net_tx_bps"] != 10.0 {
		t.Errorf("rates = %v / %v", body["net_rx_bps"], body["net_tx_bps"])
	}
	want := map[string]any{"app.demo-app.up": 1, "app.demo-app.restarts": 0, "app.demo-app.cpu": 1.5, "app.demo-app.mem": int64(10485760)}
	for k, v := range want {
		if body[k] != v {
			t.Errorf("%s = %#v, want %#v", k, body[k], v)
		}
	}
	if body["agent_errors"] != int64(0) {
		t.Errorf("agent_errors not reset: %v", body["agent_errors"])
	}
	if len(events) != 1 || events[0]["type"] != "app.started" || events[0]["app"] != "demo-app" {
		t.Errorf("second events = %v", events)
	}
	if len(f.cursors) != 2 || f.cursors[0] != "" || f.cursors[1] != "c1" {
		t.Errorf("journal cursors = %q", f.cursors)
	}
}

func TestDockerFailureKeepsBaseline(t *testing.T) {
	f := &fakeHelper{}
	a := newApps(f)
	a.observe(context.Background()) // baseline: nothing
	a.observe(context.Background()) // demo-app started
	f.noDocker = true
	if m, e := a.observe(context.Background()); m != nil || e != nil {
		t.Errorf("failed look = %v, %v", m, e)
	}
	f.noDocker = false
	if _, e := a.observe(context.Background()); len(e) != 0 {
		t.Errorf("after a failed look, events = %v (want none: the baseline was kept)", e)
	}
}

func TestPublishOverMQTT(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	d := agenttest.Deps(t, fakecp.New(t), nil, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	host, port := strings.Split(b.Addr, ":")[0], strings.Split(b.Addr, ":")[1]
	go func() { // owned by the test: ends with cancel
		_ = d.Bus.Run(ctx, settingsFor(host, port, b.CAFile), nil)
	}()
	f := &fakeHelper{journal: []helper.Warning{{Cursor: "c1", Message: "disk almost full"}}}
	m := &Module{Interval: 50 * time.Millisecond}
	agenttest.Start(t, moduleWith(m, f), d)
	ok := b.Wait(5*time.Second, func() bool {
		var metrics, logs int
		for _, p := range b.Published() {
			switch p.Topic {
			case "devices/d-1/metrics":
				metrics++
			case "devices/d-1/events":
				if strings.Contains(p.Payload, `"type":"log"`) && strings.Contains(p.Payload, `"timestamp"`) {
					logs++
				}
			}
		}
		return metrics >= 2 && logs == 1
	})
	if !ok {
		t.Fatalf("published %+v", b.Published())
	}
}

func TestInterval(t *testing.T) {
	t.Setenv(IntervalEnv, "")
	if got := (&Module{}).interval(); got != DefaultInterval {
		t.Errorf("default = %v", got)
	}
	t.Setenv(IntervalEnv, "1.5")
	if got := (&Module{}).interval(); got != 1500*time.Millisecond {
		t.Errorf("env = %v", got)
	}
	if got := (&Module{Interval: time.Second}).interval(); got != time.Second {
		t.Errorf("field = %v", got)
	}
}

func TestEventsWaitWhileDisconnected(t *testing.T) {
	d := agenttest.Deps(t, fakecp.New(t), nil, nil)
	f := &fakeHelper{journal: []helper.Warning{{Cursor: "c1", Message: "disk almost full"}}}
	o := newObserver(d)
	o.helper, o.apps = f, newApps(f)
	o.publish(context.Background()) // the router is not connected: nothing goes out
	if len(o.pending) != 1 || o.pending[0]["timestamp"] == nil || o.unsent == nil {
		t.Fatalf("pending = %v, unsent = %v", o.pending, o.unsent)
	}
	o.unsent["marker"] = true
	for range maxPending + 10 {
		o.pending = append(o.pending, map[string]any{"type": "x"})
	}
	o.publish(context.Background())
	if len(o.pending) != maxPending {
		t.Errorf("pending = %d, want the cap %d", len(o.pending), maxPending)
	}
	if o.unsent == nil || o.unsent["marker"] != nil || o.unsent["timestamp"] == nil {
		t.Error("the newest unsent metrics body must replace the older one")
	}
}

func TestJournalWarningsAreRedacted(t *testing.T) {
	d := agenttest.Deps(t, fakecp.New(t), nil, nil)
	f := &fakeHelper{journal: []helper.Warning{{Cursor: "c1", Message: "auth password=s3cret-pass failed"}}}
	o := newObserver(d)
	o.helper, o.apps = f, newApps(f)
	_, events := o.collect(context.Background())
	if len(events) != 1 {
		t.Fatalf("events = %v", events)
	}
	msg, _ := events[0]["message"].(string)
	if strings.Contains(msg, "s3cret") || !strings.Contains(msg, "[redacted]") {
		t.Errorf("message = %q", msg)
	}
}

func TestBuffersDropOnIdentityChange(t *testing.T) {
	d := agenttest.Deps(t, fakecp.New(t), nil, nil)
	f := &fakeHelper{journal: []helper.Warning{{Cursor: "c1", Message: "disk almost full"}}}
	o := newObserver(d)
	o.helper, o.apps = f, newApps(f)
	o.publish(context.Background())
	if o.unsent == nil || len(o.pending) == 0 {
		t.Fatal("expected buffers while disconnected")
	}
	o.deviceID = "old-device"
	// Identity still reports d-1 from the test fixture: treat as a change.
	o.dropBuffersIfIdentityChanged()
	if o.unsent != nil || len(o.pending) != 0 {
		t.Errorf("buffers after identity change: pending=%d unsent=%v", len(o.pending), o.unsent != nil)
	}
}
