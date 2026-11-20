package mqtt

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/fakebroker"
	"meteorcloud/meteorcli/internal/fakecp"
)

const prefix = "devices/" + agenttest.DeviceID + "/"

func payloads(b *fakebroker.Broker, topic string) []map[string]any {
	var out []map[string]any
	for _, p := range b.Published() {
		if p.Topic == topic {
			var m map[string]any
			_ = json.Unmarshal([]byte(p.Payload), &m)
			out = append(out, m)
		}
	}
	return out
}

func TestSessionStatusAndCommands(t *testing.T) {
	b, err := fakebroker.Start(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer b.Stop()
	d := agenttest.Deps(t, fakecp.New(t), &agenttest.MQTT{Addr: b.Addr, CAFile: b.CAFile}, map[string]string{"/proc/uptime": "123.4 5.0\n"})
	software := d.Signals.Subscribe(agent.Is(agent.WorkWaiting{Kind: "software"}))
	defer software.Close()

	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := (&Module{Reconfirm: 50 * time.Millisecond}).Run(ctx, d); err != nil {
			t.Errorf("Run: %v", err)
		}
	}()
	t.Cleanup(func() {
		cancel()
		wg.Wait()
	})

	// online right after connect, and once more shortly after.
	if !b.Wait(5*time.Second, func() bool { return len(payloads(b, prefix+"status")) >= 2 }) {
		t.Fatalf("published %+v", b.Published())
	}
	online := payloads(b, prefix+"status")[0]
	if online["status"] != "online" || online["agent_version"] != "1.2.3" || online["uptime_seconds"] != float64(123) {
		t.Errorf("online = %v", online)
	}
	if ts, _ := online["timestamp"].(string); !strings.HasSuffix(ts, "Z") || len(ts) != 20 {
		t.Errorf("timestamp = %q", online["timestamp"])
	}
	c := b.Connects()[0]
	if c.ClientID != "device-d-1" || c.Username != "device_d-1" || c.Password != "mqtt_pw" || c.Will != offline {
		t.Errorf("connect = %+v", c)
	}

	b.Send(prefix+"commands", `{"type":"ping","command_id":"c-1"}`)
	b.Send(prefix+"commands", `not json`)
	b.Send(prefix+"commands", `{"type":"reboot_now"}`)
	b.Send(prefix+"commands", `{"type":"sync_desired","revision":3}`)
	if !b.Wait(5*time.Second, func() bool { return len(payloads(b, prefix+"commands/result")) == 1 }) {
		t.Fatal("no pong")
	}
	pong := payloads(b, prefix+"commands/result")[0]
	if pong["command_id"] != "c-1" || pong["status"] != "completed" || pong["result"].(map[string]any)["message"] != "pong" {
		t.Errorf("pong = %v", pong)
	}
	select {
	case <-software.C():
	case <-time.After(5 * time.Second):
		t.Error("sync_desired did not become a WorkWaiting signal")
	}

	statusBefore := len(payloads(b, prefix+"status"))
	b.Send(prefix+"commands", `{"type":"probe","command_id":"c-probe"}`)
	if !b.Wait(5*time.Second, func() bool {
		results := payloads(b, prefix+"commands/result")
		statuses := payloads(b, prefix+"status")
		if len(results) < 2 || len(statuses) <= statusBefore {
			return false
		}
		last := results[len(results)-1]
		return last["command_id"] == "c-probe" && last["result"].(map[string]any)["message"] == "probed"
	}) {
		t.Fatalf("no probe: results=%v status=%v", payloads(b, prefix+"commands/result"), payloads(b, prefix+"status"))
	}
	var probed map[string]any
	for _, s := range payloads(b, prefix+"status") {
		if s["probe"] == true && s["command_id"] == "c-probe" {
			probed = s
		}
	}
	if probed == nil || probed["status"] != "online" {
		t.Errorf("probe status = %v", probed)
	}

	// New credentials: the session starts over with them.
	data, _ := os.ReadFile(d.Paths.MQTTFile())
	if err := os.WriteFile(d.Paths.MQTTFile(), []byte(strings.Replace(string(data), "mqtt_pw", "mqtt_pw2", 1)), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := d.Identity.Load(); err != nil {
		t.Fatal(err)
	}
	d.Signals.Publish(agent.CredentialsRotated{})
	if !b.Wait(5*time.Second, func() bool {
		cs := b.Connects()
		return len(cs) >= 2 && cs[len(cs)-1].Password == "mqtt_pw2"
	}) {
		t.Fatalf("no reconnect with the new password: %+v", b.Connects())
	}

	cancel()
	wg.Wait()
	all := payloads(b, prefix+"status")
	if last := all[len(all)-1]; last["status"] != "offline" {
		t.Errorf("last status = %v, want offline on stop", last)
	}
	if d.Bus.State().Connected {
		t.Error("still connected after stop")
	}
}

func TestWaitsWithoutSettings(t *testing.T) {
	d := agenttest.Deps(t, fakecp.New(t), nil, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if err := (&Module{}).Run(ctx, d); err != nil {
		t.Fatal(err)
	}
	if d.Bus.State().Connected {
		t.Error("connected without settings")
	}
}

func TestSettingsUseAPIHostForLoopbackBroker(t *testing.T) {
	cp := fakecp.New(t) // http://127.0.0.1:port: loopback, so the stored host is kept
	d := agenttest.Deps(t, cp, &agenttest.MQTT{Addr: "localhost:8883"}, nil)
	s, ok := settings(d)
	if !ok || s.Host != "localhost" || s.Port != 8883 || s.Will != offline || s.CAFile != "" {
		t.Errorf("settings = %+v, %v", s, ok)
	}
}
