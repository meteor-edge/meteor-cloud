// Package mqtt is the module that keeps the device's MQTT session up: it runs
// the bus with the credentials identity stored, starts over when they change,
// says "online" (retained) after every connect, and answers commands: ping
// gets a pong on commands/result, sync_desired becomes a WorkWaiting signal.
// Other modules publish through Deps.Bus; they never see the connection. It is
// critical: a broken broker must never stop the heartbeat, and this module
// never stops trying.
package mqtt

import (
	"context"
	"encoding/json"
	"net/url"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/bus"
)

// name is the module's id.
const name = "mqtt"

// Timing of the module.
const (
	// reconfirm publishes online once more after a connect, so a stale will
	// "offline" of the previous session cannot win the race (Python: 2 s).
	reconfirm = 2 * time.Second
	// tickEvery feeds the watchdog and rechecks the settings.
	tickEvery = 30 * time.Second
	// inboxSize bounds commands waiting for this module's loop.
	inboxSize = 32
)

// offline is the status payload of the will and of a clean stop.
const offline = `{"status":"offline"}`

// Module runs the MQTT session.
type Module struct {
	// Reconfirm overrides reconfirm (tests); zero means 2 s.
	Reconfirm time.Duration
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return true }

// session is one running bus.Run.
type session struct {
	settings bus.Settings
	cancel   context.CancelFunc
	done     chan struct{}
}

func (s *session) stop() {
	if s == nil {
		return
	}
	s.cancel()
	<-s.done
}

// Run implements agent.Module.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	changes := d.Signals.Subscribe(func(s agent.Signal) bool {
		switch s.(type) {
		case agent.CredentialsRotated, agent.ConfigChanged, agent.Enrolled:
			return true
		}
		return false
	})
	defer changes.Close()
	commands, stopInbox := d.Bus.Inbox(bus.Commands, inboxSize)
	defer stopInbox()
	tick := time.NewTicker(tickEvery)
	defer tick.Stop()

	var cur *session
	defer func() { cur.stop() }()
	waiting := false
	apply := func() {
		s, ok := settings(d)
		if cur != nil && ok && s == cur.settings {
			select {
			case <-cur.done:
				// Session finished (e.g. unreadble CA at start); restart below.
			default:
				return
			}
		}
		if cur != nil {
			select {
			case <-cur.done:
			default:
				d.Log.Info("MQTT settings changed; reconnecting")
			}
		}
		cur.stop()
		cur = nil
		if !ok {
			if !waiting {
				d.Log.Info("no MQTT settings yet (or MQTT is off on the server); waiting")
			}
			waiting = true
			return
		}
		waiting = false
		sctx, cancel := context.WithCancel(ctx)
		cur = &session{settings: s, cancel: cancel, done: make(chan struct{})}
		done := cur.done
		go func() { // owned: session.stop waits for it, and Run stops it before returning
			defer close(done)
			if err := d.Bus.Run(sctx, s, m.online(d)); err != nil {
				d.Log.Error("MQTT session", "err", err)
			}
		}()
	}

	apply()
	for {
		d.Health.Tick(name)
		var sessionDone <-chan struct{}
		if cur != nil {
			sessionDone = cur.done
		}
		select {
		case <-ctx.Done():
			return nil
		case <-changes.C():
			changes.Take()
			apply()
		case <-tick.C:
			apply()
		case <-sessionDone:
			apply()
		case msg := <-commands:
			m.command(ctx, d, msg)
		}
	}
}

// settings builds the bus settings from the identity. ok is false while the
// device has no MQTT credentials or the server turned MQTT off.
func settings(d agent.Deps) (bus.Settings, bool) {
	mq, ok := d.Identity.MQTT()
	id := d.Identity.DeviceID()
	if !ok || id == "" {
		return bus.Settings{}, false
	}
	apiHost := ""
	if u, err := url.Parse(d.API.BaseURL()); err == nil {
		apiHost = u.Hostname()
	}
	port := mq.Port
	if port == 0 {
		port = 8883
	}
	s := bus.Settings{
		DeviceID: id, Host: bus.BrokerHost(mq.Host, apiHost), Port: port,
		Username: mq.Username, Password: mq.Password, Will: offline,
	}
	if mq.CACertPath != nil {
		s.CAFile = *mq.CACertPath
	}
	return s, true
}

// online returns the hook that runs on every connect: say online now and once
// more shortly after.
func (m *Module) online(d agent.Deps) func(ctx context.Context) {
	again := m.Reconfirm
	if again <= 0 {
		again = reconfirm
	}
	return func(ctx context.Context) {
		m.publishOnline(ctx, d)
		t := time.NewTimer(again)
		defer t.Stop()
		select {
		case <-ctx.Done():
		case <-t.C:
			m.publishOnline(ctx, d)
		}
	}
}

func (m *Module) publishOnline(ctx context.Context, d agent.Deps) {
	status := map[string]any{
		"status":        "online",
		"timestamp":     bus.Timestamp(time.Now()),
		"agent_version": d.Version,
	}
	if up, ok := d.Host.Uptime(); ok {
		status["uptime_seconds"] = up
	}
	if err := d.Bus.PublishJSON(ctx, bus.Status, status, true); err != nil && ctx.Err() == nil {
		d.Log.Warn("could not publish the online status", "err", err)
	}
}

// command handles one message from devices/{id}/commands. Messages it cannot
// parse and unknown types are ignored, as the contract says.
func (m *Module) command(ctx context.Context, d agent.Deps, msg bus.Message) {
	var c struct {
		Type      string `json:"type"`
		CommandID string `json:"command_id"`
		Revision  *int   `json:"revision"`
	}
	if err := json.Unmarshal([]byte(msg.Payload), &c); err != nil {
		d.Log.Debug("ignored a command that is not JSON")
		return
	}
	switch c.Type {
	case "ping":
		if c.CommandID == "" {
			return
		}
		result := map[string]any{"command_id": c.CommandID, "status": "completed", "result": map[string]string{"message": "pong"}}
		if err := d.Bus.PublishJSON(ctx, bus.CommandsResult, result, false); err != nil {
			d.Log.Warn("could not answer ping", "err", err)
		}
	case "sync_desired":
		// A hint only: the deploy module lists desired software on its next cycle anyway.
		d.Signals.Publish(agent.WorkWaiting{Kind: "software"})
	case "sync_actions":
		// Also a hint: the heartbeat says that actions wait, too.
		d.Signals.Publish(agent.WorkWaiting{Kind: "actions"})
	default:
		d.Log.Debug("ignored a command of an unknown type", "type", c.Type)
	}
}
