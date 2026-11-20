package mqttfwd

import (
	"context"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
)

// retainedTopic carries the retained device status (online/offline, and the
// agent's last will).
const retainedTopic = "devices/+/status"

// How long replayRetained waits: until no retained message arrived for
// retainedQuiet, and never longer than retainedMax.
var (
	retainedQuiet = 2 * time.Second
	retainedMax   = 60 * time.Second
)

// replayRetained gets the retained device status after a connect. MQTT 5 never
// sends retained messages on a shared subscription, but the Python gateway (a
// plain subscription) got every device's retained status on connect, so the
// control plane caught up with devices that changed state while it was away.
// A plain subscription to devices/+/status does that here; it is removed once
// the retained messages have arrived. Live status messages that arrive in that
// window come twice (shared and plain): two copies of one last-value update,
// one after the other.
func (s *Session) replayRetained(ctx context.Context, cm *autopaho.ConnectionManager, gen uint64) {
	start := time.Now()
	s.lastRetained.Store(start.UnixNano())
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	_, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: []paho.SubscribeOptions{{Topic: retainedTopic, QoS: 1}}})
	cancel()
	if err != nil {
		s.log.Warn("retained device status not replayed", "error", err.Error())
		return
	}
	for {
		quiet := time.Since(time.Unix(0, s.lastRetained.Load()))
		if quiet >= retainedQuiet || time.Since(start) >= retainedMax {
			break
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(retainedQuiet - quiet):
		}
		if s.connGen.Load() != gen {
			return // the next connect replays again (and removes the subscription)
		}
	}
	uctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: []string{retainedTopic}}); err != nil {
		s.log.Warn("removing the retained status subscription failed", "error", err.Error())
		return
	}
	s.log.Info("retained device status replayed", "took", time.Since(start).Round(time.Millisecond))
}
