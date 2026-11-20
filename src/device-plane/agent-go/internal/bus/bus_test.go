package bus

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/eclipse/paho.golang/paho"

	"meteorcloud/meteorcli/internal/fakebroker"
)

const devID = "11111111-2222-3333-4444-555555555555"

func quiet() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func settings(t *testing.T, b *fakebroker.Broker) Settings {
	t.Helper()
	host, port, err := net.SplitHostPort(b.Addr)
	if err != nil {
		t.Fatal(err)
	}
	p, _ := strconv.Atoi(port)
	return Settings{DeviceID: devID, Host: host, Port: p, Username: "device_x", Password: "mqtt_secret", CAFile: b.CAFile, Will: `{"status":"offline"}`}
}

// start runs the router's session in the background and returns a stop
// function that cancels it and waits for Run to return.
func start(t *testing.T, r *Router, s Settings, onUp func(context.Context)) func() {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx, s, onUp) }()
	stopped := false
	stop := func() {
		if stopped {
			return
		}
		stopped = true
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("Run: %v", err)
			}
		case <-time.After(10 * time.Second):
			t.Fatal("Run did not return after cancel")
		}
	}
	t.Cleanup(stop)
	return stop
}

func waitUp(t *testing.T, r *Router) {
	t.Helper()
	select {
	case <-r.Up():
	case <-time.After(10 * time.Second):
		t.Fatalf("not connected: %+v", r.State())
	}
}

func TestConnectPublishCommandAndCleanStop(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	r := NewRouter(quiet())
	onUp := make(chan struct{}, 4)
	stop := start(t, r, settings(t, b), func(ctx context.Context) {
		onUp <- struct{}{}
		_ = r.Publish(ctx, Status, []byte(`{"status":"online"}`), true)
	})
	waitUp(t, r)
	select {
	case <-onUp:
	case <-time.After(5 * time.Second):
		t.Fatal("onUp did not run")
	}

	c := b.Connects()[0]
	if c.ClientID != "device-"+devID || c.Username != "device_x" || c.Password != "mqtt_secret" {
		t.Errorf("connect = %+v", c)
	}
	if c.WillTopic != "devices/"+devID+"/status" || c.Will != `{"status":"offline"}` {
		t.Errorf("will = %q on %q", c.Will, c.WillTopic)
	}
	if !r.State().Connected || r.State().Broker != b.Addr {
		t.Errorf("state = %+v", r.State())
	}

	if err := r.Publish(context.Background(), Metrics, []byte(`{"cpu_percent":1}`), false); err != nil {
		t.Fatal(err)
	}
	if err := r.Publish(context.Background(), "commands", nil, false); err == nil {
		t.Error("publishing on commands must be refused")
	}

	in, stopInbox := r.Inbox(Commands, 4)
	defer stopInbox()
	_, next := r.Recent(context.Background(), 0, true, 0)
	if !b.Wait(5*time.Second, func() bool { return b.Send("devices/"+devID+"/commands", `{"type":"ping","command_id":"c1"}`) == 1 }) {
		t.Fatal("the session did not subscribe to commands")
	}
	select {
	case m := <-in:
		if m.Payload != `{"type":"ping","command_id":"c1"}` || m.Topic != "devices/"+devID+"/commands" {
			t.Errorf("message = %+v", m)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no command delivered")
	}
	msgs, _ := r.Recent(context.Background(), next, false, time.Second)
	if len(msgs) != 1 || msgs[0].Seq != next+1 {
		t.Errorf("Recent = %+v (after %d)", msgs, next)
	}

	stop()
	if r.State().Connected {
		t.Error("still connected after stop")
	}
	if !errors.Is(r.Publish(context.Background(), Metrics, nil, false), ErrNotConnected) {
		t.Error("publish after stop must fail with ErrNotConnected")
	}
	var statuses []string
	for _, p := range b.Published() {
		if p.Topic == "devices/"+devID+"/status" {
			if !p.Retain || p.QoS != 1 {
				t.Errorf("status published with retain=%v qos=%d", p.Retain, p.QoS)
			}
			statuses = append(statuses, p.Payload)
		}
	}
	if len(statuses) != 2 || statuses[1] != `{"status":"offline"}` {
		t.Errorf("status messages = %q, want online then offline", statuses)
	}
}

func TestReconnectsAfterBrokerOutage(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	r := NewRouter(quiet())
	var mu sync.Mutex
	ups := 0
	start(t, r, settings(t, b), func(context.Context) {
		mu.Lock()
		ups++
		mu.Unlock()
	})
	waitUp(t, r)

	b.Stop()
	deadline := time.Now().Add(5 * time.Second)
	for r.State().Connected && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if r.State().Connected {
		t.Fatal("still connected while the broker is down")
	}
	if !errors.Is(r.Publish(context.Background(), Metrics, nil, false), ErrNotConnected) {
		t.Error("publish while down must fail with ErrNotConnected")
	}
	time.Sleep(1500 * time.Millisecond) // at least one failed attempt
	if r.State().LastError == "" {
		t.Error("no last error recorded while the broker is down")
	}
	if err := b.Restart(); err != nil {
		t.Fatal(err)
	}
	waitUp(t, r)
	count := func() int {
		mu.Lock()
		defer mu.Unlock()
		return ups
	}
	b.Wait(5*time.Second, func() bool { return count() >= 2 })
	if got := count(); got != 2 {
		t.Errorf("onUp ran %d times, want 2 (one per connection)", got)
	}
}

func TestRefusedLoginIsExplained(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	b.Refuse = 0x86
	r := NewRouter(quiet())
	start(t, r, settings(t, b), nil)
	deadline := time.Now().Add(5 * time.Second)
	for r.State().LastError == "" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if got := r.State().LastError; !strings.Contains(got, "refused the MQTT credentials") {
		t.Errorf("last error = %q", got)
	}
}

func TestWrongCAFails(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	other, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	other.Stop()
	s := settings(t, b)
	s.CAFile = other.CAFile
	r := NewRouter(quiet())
	start(t, r, s, nil)
	deadline := time.Now().Add(5 * time.Second)
	for r.State().LastError == "" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if got := r.State().LastError; !strings.Contains(got, "not signed by the CA") {
		t.Errorf("last error = %q", got)
	}
	if len(b.Connects()) != 0 {
		t.Error("connected to a broker the CA does not vouch for")
	}
}

func TestRunRejectsIncompleteSettings(t *testing.T) {
	r := NewRouter(quiet())
	if err := r.Run(context.Background(), Settings{}, nil); err == nil {
		t.Error("Run with empty settings must fail")
	}
}

type fakePub struct{ got []*paho.Publish }

func (f *fakePub) Publish(_ context.Context, p *paho.Publish) (*paho.PublishResponse, error) {
	f.got = append(f.got, p)
	return &paho.PublishResponse{}, nil
}

func TestPublishUsesQoS1AndDeviceTopic(t *testing.T) {
	r := NewRouter(quiet())
	f := &fakePub{}
	r.setUp(f, devID, "b:8883")
	if err := r.PublishJSON(context.Background(), Events, map[string]string{"type": "x"}, false); err != nil {
		t.Fatal(err)
	}
	if len(f.got) != 1 || f.got[0].Topic != "devices/"+devID+"/events" || f.got[0].QoS != 1 || f.got[0].Retain {
		t.Errorf("published %+v", f.got)
	}
}

func TestDeliverIgnoresForeignTopicsAndDropsWhenFull(t *testing.T) {
	r := NewRouter(quiet())
	r.setDeviceID(devID)
	in, stop := r.Inbox(Commands, 1)
	defer stop()
	r.deliver("devices/other/commands", []byte("x"))
	r.deliver("devices/"+devID+"/status", []byte("x"))
	r.deliver("devices/"+devID+"/commands", []byte("a"))
	r.deliver("devices/"+devID+"/commands", []byte("b")) // inbox full: dropped there, kept in Recent
	if m := <-in; m.Payload != "a" {
		t.Errorf("first = %q", m.Payload)
	}
	select {
	case m := <-in:
		t.Errorf("unexpected %q", m.Payload)
	default:
	}
	msgs, next := r.Recent(context.Background(), 0, false, 0)
	if len(msgs) != 2 || next != 2 {
		t.Errorf("Recent = %+v next %d", msgs, next)
	}
	for i := 0; i < recentSize+5; i++ {
		r.deliver("devices/"+devID+"/commands", []byte("n"))
	}
	if msgs, _ := r.Recent(context.Background(), 0, false, 0); len(msgs) != recentSize {
		t.Errorf("Recent kept %d, want %d", len(msgs), recentSize)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if msgs, _ := r.Recent(ctx, 1000, false, time.Minute); msgs != nil {
		t.Error("Recent with a cancelled context must return at once with nothing")
	}
}

func TestResolveTopic(t *testing.T) {
	tests := []struct {
		raw     string
		publish bool
		want    string
		err     string
	}{
		{"", true, Status, ""},
		{"", false, Commands, ""},
		{"metrics", true, Metrics, ""},
		{"devices/" + devID + "/commands/result", true, CommandsResult, ""},
		{"devices/other/status", true, "", "must be under"},
		{"devices/" + devID + "/#", false, "", "wildcards"},
		{"status", false, "", "must be one of: commands"},
		{"commands", true, "", "must be one of"},
	}
	for _, tt := range tests {
		got, err := ResolveTopic(devID, tt.raw, tt.publish)
		if tt.err != "" {
			if err == nil || !strings.Contains(err.Error(), tt.err) {
				t.Errorf("ResolveTopic(%q, %v) err = %v, want %q", tt.raw, tt.publish, err, tt.err)
			}
			continue
		}
		if err != nil || got != tt.want {
			t.Errorf("ResolveTopic(%q, %v) = %q, %v; want %q", tt.raw, tt.publish, got, err, tt.want)
		}
	}
}

func TestBrokerHost(t *testing.T) {
	tests := []struct{ stored, api, want string }{
		{"mqtt.example.com", "api.example.com", "mqtt.example.com"},
		{"localhost", "192.168.1.10", "192.168.1.10"},
		{"127.0.0.1", "", "127.0.0.1"},
		{"", "localhost", "localhost"},
		{"::1", "meteor.lan", "meteor.lan"},
	}
	for _, tt := range tests {
		if got := BrokerHost(tt.stored, tt.api); got != tt.want {
			t.Errorf("BrokerHost(%q, %q) = %q, want %q", tt.stored, tt.api, got, tt.want)
		}
	}
}
