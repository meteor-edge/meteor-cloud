package mqttfwd

import (
	"context"
	"fmt"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/eclipse/paho.golang/paho"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

func TestFilterCovers(t *testing.T) {
	tests := []struct {
		outer, inner string
		want         bool
	}{
		{"devices/+/status", "devices/abc/status", true},
		{"devices/+/status", "devices/+/status", true},
		{"devices/+/status", "devices/abc/#", false},
		{"devices/+/status", "devices/#", false},
		{"devices/+/status", "#", false},
		{"devices/+/status", "devices/abc/status/x", false},
		{"devices/+/status", "devices/abc", false},
		{"devices/+/commands/result", "devices/abc/commands/result", true},
		{"devices/+/commands/result", "devices/abc/commands/+", false},
		{"devices/#", "devices/abc/#", true},
		{"#", "anything/at/all", true},
		{"lab/temp", "lab/+", false},
	}
	for _, tt := range tests {
		if got := filterCovers(tt.outer, tt.inner); got != tt.want {
			t.Errorf("filterCovers(%q, %q) = %v, want %v", tt.outer, tt.inner, got, tt.want)
		}
	}
	for filter, want := range map[string]bool{
		"devices/abc/status": true, "devices/+/events": true, "devices/abc/#": false, "lab/temp": false, "#": false,
	} {
		if got := coveredByDefault(filter); got != want {
			t.Errorf("coveredByDefault(%q) = %v, want %v", filter, got, want)
		}
	}
	for topic, want := range map[string]bool{
		"devices/abc/status": true, "devices/abc/commands/result": true, "devices/abc/commands": false,
		"devices/abc/other": false, "$SYS/devices/x/status": false,
	} {
		if got := matchesDefault(topic); got != want {
			t.Errorf("matchesDefault(%q) = %v, want %v", topic, got, want)
		}
	}
	if !topicMatches("$SYS/#", "$SYS/broker") || topicMatches("#", "$SYS/broker") {
		t.Error("$-topics: only a $ filter matches them")
	}
}

func TestWatchDuplicateRules(t *testing.T) {
	id := func(n int) *paho.PublishProperties { return &paho.PublishProperties{SubscriptionIdentifier: &n} }
	shared := &Session{cfg: Config{SharedGroup: "g"}}
	shared.subIDs.Store(true)
	tests := []struct {
		name string
		pub  paho.Publish
		want bool
	}{
		{"watch copy of a live status", paho.Publish{Topic: "devices/d/status", Properties: id(watchSubID)}, true},
		{"watch copy of the retained status", paho.Publish{Topic: "devices/d/status", Retain: true, Properties: id(watchSubID)}, false},
		{"watch copy of a non-device topic", paho.Publish{Topic: "devices/d/other", Properties: id(watchSubID)}, false},
		{"replay copy of a live status", paho.Publish{Topic: "devices/d/status", Properties: id(replaySubID)}, true},
		{"replay copy of a retained status", paho.Publish{Topic: "devices/d/status", Retain: true, Properties: id(replaySubID)}, false},
		{"shared copy", paho.Publish{Topic: "devices/d/status"}, false},
	}
	for _, tt := range tests {
		if got := shared.watchDuplicate(&tt.pub); got != tt.want {
			t.Errorf("%s: drop = %v, want %v", tt.name, got, tt.want)
		}
	}
	if shared.skipWatch("devices/d/status") {
		t.Error("with subscription ids a covered watch is subscribed (for its retained status)")
	}
	for name, s := range map[string]*Session{"no ids": {cfg: Config{SharedGroup: "g"}}, "no shared group": {}} {
		s.subIDs.Store(name == "no shared group")
		if s.watchDuplicate(&paho.Publish{Topic: "devices/d/status", Properties: id(watchSubID)}) {
			t.Errorf("%s: nothing may be dropped", name)
		}
		if !s.skipWatch("devices/d/status") || s.skipWatch("devices/d/#") {
			t.Errorf("%s: only covered watches are skipped", name)
		}
	}
}

// The console's Listen panel on a device page watches devices/<id>/status;
// every live status must still reach the control plane once (it was twice: the
// shared device subscription and the watch both delivered it), while the
// retained status still shows up when listening starts.
func TestBrokerWatchOverlappingDeviceTopicsIngestsOnce(t *testing.T) {
	broker := brokerURL(t)
	cp := &controlPlane{got: map[string][]string{}}
	srv := httptest.NewServer(cp)
	defer srv.Close()
	// A status retained before the forwarder starts: it arrives through the
	// retained replay, so the replay's subscription is in place from then on.
	// Published before that, the status below would be replayed as well.
	ctx := context.Background()
	probe := fmt.Sprintf("devices/probe-%d/status", time.Now().UnixNano())
	pub := devicePublisher(t, broker)
	retain := func(payload string) {
		if _, err := pub.Publish(ctx, &paho.Publish{Topic: probe, QoS: 1, Retain: true, Payload: []byte(payload)}); err != nil {
			t.Error(err)
		}
	}
	retain(`{"status":"online"}`)
	defer retain("") // clear it
	fwd := startForwarder(t, broker, srv.URL)
	defer fwd.stop()
	if !fwd.sess.dedupWatches() {
		t.Skip("the broker does not support subscription identifiers")
	}
	waitFor(t, func() bool { return cp.count(probe) == 1 })
	dev := fmt.Sprintf("watch-%d", time.Now().UnixNano())
	status, other := "devices/"+dev+"/status", "devices/"+dev+"/other"
	dropped := func() float64 { return testutil.ToFloat64(fwd.metrics.WatchDuplicatesDropped) }

	fwd.sess.Publish(ctx, status, `{"status":"online","n":1}`, 1, true) // retained
	defer fwd.sess.Publish(ctx, status, "", 1, true)                    // clear it
	waitFor(t, func() bool { return cp.count(status) == 1 })

	// Listening starts: the retained status comes once more, through the watch.
	if err := fwd.sess.Watch(ctx, status); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return cp.count(status) == 2 })

	// A live status: the shared copy is forwarded, the watch copy dropped (and
	// the retained replay's copy, if its window is still open).
	before := dropped()
	fwd.sess.Publish(ctx, status, `{"status":"online","n":2}`, 1, false)
	if !waitUntil(func() bool { return cp.count(status) == 3 && dropped() >= before+1 }) {
		t.Fatalf("after a live status: status=%q dropped=%v (before %v)", got(cp, status), dropped(), before)
	}

	// A partly overlapping watch: its own topics come through, device ones once.
	fwd.sess.Watch(ctx, "devices/"+dev+"/#")
	if !waitUntil(func() bool { return cp.count(status) == 4 }) { // its retained status
		t.Fatalf("after the second watch: status=%q dropped=%v", got(cp, status), dropped())
	}
	before = dropped()
	fwd.sess.Publish(ctx, status, `{"status":"online","n":3}`, 1, false)
	fwd.sess.Publish(ctx, other, "x", 1, false)
	waitFor(t, func() bool { return cp.count(other) == 1 && cp.count(status) == 5 && dropped() >= before+1 })
	time.Sleep(200 * time.Millisecond) // a late duplicate would land here
	if n := cp.count(status); n != 5 {
		t.Fatalf("status forwarded %d times, want 5: %q", n, got(cp, status))
	}
}

func waitUntil(cond func() bool) bool {
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(10 * time.Millisecond)
	}
	return true
}

// got copies what the fake control plane received on topic (never t.Fatal
// while holding its lock: stopping the forwarder still needs it).
func got(cp *controlPlane, topic string) []string {
	cp.mu.Lock()
	defer cp.mu.Unlock()
	return append([]string(nil), cp.got[topic]...)
}
