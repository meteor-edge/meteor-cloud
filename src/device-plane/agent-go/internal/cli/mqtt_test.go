package cli

import (
	"encoding/json"
	"net"
	"strconv"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/fakebroker"
)

// agentWithBroker runs the agent with MQTT settings for a fake broker and
// waits until it is connected.
func agentWithBroker(t *testing.T, groups groupsOf) (string, *fakebroker.Broker) {
	t.Helper()
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(b.Stop)
	host, port, _ := net.SplitHostPort(b.Addr)
	n, _ := strconv.Atoi(port)
	mq, _ := json.Marshal(map[string]any{
		"version": 1, "host": host, "port": n, "username": "device_d-1", "password": "mqtt_secret_pw",
		"tls": true, "ca_cert_path": b.CAFile,
	})
	root := runningAgentWith(t, groups, map[string]string{"var/lib/meteorcli/identity/mqtt.json": string(mq)})
	if !b.Wait(10*time.Second, func() bool {
		for _, p := range b.Published() {
			if p.Topic == "devices/d-1/status" {
				return true
			}
		}
		return false
	}) {
		t.Fatal("the agent never connected to the broker")
	}
	return root, b
}

func TestMQTTTestPublishesThroughTheAgent(t *testing.T) {
	root, b := agentWithBroker(t, groupsOf{agent.GroupAdmin, agent.GroupRead})

	r := meteorcli(t, root, nil, "mqtt-test")
	if r.code != 0 || !strings.Contains(r.stdout, "Published MQTT test message to devices/d-1/status") {
		t.Fatalf("mqtt-test = %+v", r)
	}
	r = meteorcli(t, root, nil, "mqtt-test", "events", `{"type":"cli.test"}`)
	if r.code != 0 {
		t.Fatalf("mqtt-test events = %+v", r)
	}
	found := false
	for _, p := range b.Published() {
		if p.Topic == "devices/d-1/events" && p.Payload == `{"type":"cli.test"}` && !p.Retain {
			found = true
		}
		if p.ClientID != "device-d-1" {
			t.Errorf("published by %q: the CLI must use the agent's session", p.ClientID)
		}
	}
	if !found {
		t.Errorf("event not published: %+v", b.Published())
	}
	for _, args := range [][]string{{"mqtt-test", "commands"}, {"mqtt-test", "devices/other/status"}, {"mqtt-test", "status", "{bad"}} {
		if r := meteorcli(t, root, nil, args...); r.code == 0 {
			t.Errorf("%v must fail: %+v", args, r)
		}
	}
	st := meteorcli(t, root, nil, "status")
	if !strings.Contains(st.stdout, "MQTT broker:    connected to "+b.Addr) {
		t.Errorf("status:\n%s", st.stdout)
	}
	if strings.Contains(st.stdout+st.stderr, "mqtt_secret_pw") {
		t.Error("status shows the MQTT password")
	}
}

func TestMQTTListenPrintsCommands(t *testing.T) {
	root, b := agentWithBroker(t, groupsOf{agent.GroupRead})
	done := make(chan result, 1)
	go func() { done <- meteorcli(t, root, nil, "mqtt-listen", "--timeout", "2") }()
	var r result
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()
	for waiting := true; waiting; {
		select {
		case r = <-done:
			waiting = false
		case <-tick.C:
			b.Send("devices/d-1/commands", `{"type":"ping","command_id":"x"}`)
		}
	}
	if r.code != 0 || !strings.Contains(r.stdout, "Listening on devices/d-1/commands") ||
		!strings.Contains(r.stdout, `devices/d-1/commands {"type":"ping","command_id":"x"}`) {
		t.Fatalf("mqtt-listen = %+v", r)
	}
	// The read group may listen but not publish as the device.
	if r := meteorcli(t, root, nil, "mqtt-test"); r.code == 0 || !strings.Contains(r.stderr, agent.GroupAdmin) {
		t.Errorf("mqtt-test with the read group only = %+v", r)
	}
}

func TestMQTTWithoutSettings(t *testing.T) {
	root := runningAgent(t, groupsOf{agent.GroupAdmin, agent.GroupRead})
	for _, cmd := range []string{"mqtt-test", "mqtt-listen"} {
		r := meteorcli(t, root, nil, cmd, "--timeout", "1")
		if cmd == "mqtt-test" {
			r = meteorcli(t, root, nil, cmd)
		}
		if r.code != 1 || !strings.Contains(r.stderr, "no MQTT settings") {
			t.Errorf("%s = %+v", cmd, r)
		}
	}
}

func TestParseWithArgs(t *testing.T) {
	fl := newFlags(&Env{Stderr: &strings.Builder{}}, "x", "")
	timeout := fl.Float64("timeout", 0, "")
	rest, code, done := parseWithArgs(fl, []string{"commands", "--timeout", "3"}, 1)
	if done || code != 0 || len(rest) != 1 || rest[0] != "commands" || *timeout != 3 {
		t.Errorf("rest=%v code=%d done=%v timeout=%v", rest, code, done, *timeout)
	}
	if _, code, done := parseWithArgs(fl, []string{"a", "b"}, 1); !done || code != 2 {
		t.Error("too many arguments must fail")
	}
}
