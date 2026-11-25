package agent

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/localrpc"
)

// MQTT over the agent socket: the CLI never reads the broker password, so
// mqtt-test publishes through the agent's own session and mqtt-listen reads
// what that session received.

// maxListenWait bounds one mqtt.listen call; the CLI calls again.
const maxListenWait = 25 * time.Second

// MQTTTestParams is the body of mqtt.test.
type MQTTTestParams struct {
	// Topic is a suffix ("status") or a full devices/<id>/... topic; empty means status.
	Topic string `json:"topic"`
	// Payload is published as is.
	Payload string `json:"payload"`
}

// MQTTTestReply answers mqtt.test.
type MQTTTestReply struct {
	Topic  string `json:"topic"`
	Broker string `json:"broker"`
}

// MQTTListenParams is the body of mqtt.listen.
type MQTTListenParams struct {
	Topic string `json:"topic"`
	// After is the Seq of the last message seen; nil starts listening now.
	After *uint64 `json:"after"`
	// WaitSeconds is how long to wait for a message (1 to 25).
	WaitSeconds int `json:"wait_seconds"`
}

// MQTTListenReply answers mqtt.listen.
type MQTTListenReply struct {
	Topic     string        `json:"topic"`
	Messages  []bus.Message `json:"messages"`
	Next      uint64        `json:"next"`
	Connected bool          `json:"connected"`
	LastError string        `json:"last_error,omitempty"`
}

// CodeMQTTUnavailable is the socket error code when there is no MQTT session.
const CodeMQTTUnavailable = "mqtt_unavailable"

func mqttHandlers(d Deps) map[string]localrpc.Handler {
	return map[string]localrpc.Handler{
		"mqtt.test": {
			Audit: func(raw json.RawMessage) []any {
				var p MQTTTestParams
				_ = json.Unmarshal(raw, &p)
				return []any{"topic", p.Topic}
			},
			Run: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
				var p MQTTTestParams
				if err := json.Unmarshal(raw, &p); err != nil {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params: %v", err)
				}
				id, err := mqttDevice(d)
				if err != nil {
					return nil, err
				}
				suffix, err := bus.ResolveTopic(id, p.Topic, true)
				if err != nil {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "%v", err)
				}
				if err := d.Bus.Publish(ctx, suffix, []byte(p.Payload), false); err != nil {
					if errors.Is(err, bus.ErrNotConnected) {
						return nil, notConnected(d)
					}
					return nil, err
				}
				return MQTTTestReply{Topic: bus.Topic(id, suffix), Broker: d.Bus.State().Broker}, nil
			},
		},
		"mqtt.listen": {
			Timeout: maxListenWait + 5*time.Second,
			Run: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
				var p MQTTListenParams
				if err := json.Unmarshal(raw, &p); err != nil {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params: %v", err)
				}
				id, err := mqttDevice(d)
				if err != nil {
					return nil, err
				}
				suffix, err := bus.ResolveTopic(id, p.Topic, false)
				if err != nil {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "%v", err)
				}
				wait := time.Duration(min(max(p.WaitSeconds, 1), int(maxListenWait/time.Second))) * time.Second
				var after uint64
				if p.After != nil {
					after = *p.After
				}
				msgs, next := d.Bus.Recent(ctx, after, p.After == nil, wait)
				st := d.Bus.State()
				return MQTTListenReply{Topic: bus.Topic(id, suffix), Messages: msgs, Next: next, Connected: st.Connected, LastError: st.LastError}, nil
			},
		},
	}
}

// mqttDevice returns the device id, or why MQTT cannot be used.
func mqttDevice(d Deps) (string, error) {
	id := d.Identity.DeviceID()
	if id == "" {
		return "", localrpc.Errorf(CodeMQTTUnavailable, "this device is not enrolled yet")
	}
	if _, ok := d.Identity.MQTT(); !ok {
		return "", localrpc.Errorf(CodeMQTTUnavailable, "no MQTT settings yet (or MQTT is off on the server)")
	}
	return id, nil
}

func notConnected(d Deps) error {
	st := d.Bus.State()
	msg := "the agent is not connected to the MQTT broker"
	if st.Broker != "" {
		msg += " " + st.Broker
	}
	if st.LastError != "" {
		msg += ": " + st.LastError
	}
	return localrpc.Errorf(CodeMQTTUnavailable, "%s", msg)
}
