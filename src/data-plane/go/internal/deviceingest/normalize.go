// Package deviceingest turns what device agents publish over MQTT into versioned
// events on NATS JetStream.
//
// Identity is never read from a payload. The device id comes from the MQTT topic,
// which the broker's ACL binds to the publishing device's own login
// (device_<uuid> may only publish under devices/<uuid>/), and the organization
// comes from the control plane's device directory. Organization or device fields
// inside a payload are ordinary data and never decide where an event goes.
package deviceingest

import (
	"bytes"
	"encoding/json"
	"strings"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/envelope"
)

// Channel is the last level of a device topic.
type Channel string

const (
	ChannelMetrics Channel = "metrics" // devices/<id>/metrics -> telemetry.v1.<org>.<device>
	ChannelStatus  Channel = "status"  // devices/<id>/status  -> device.status.v1.<org>.<device>
	ChannelEvents  Channel = "events"  // devices/<id>/events  -> device.events.v1.<org>.<device>
)

// Platform streams of device events. They are under the reserved "meteor." prefix,
// so no application can publish into them through the application data API.
const (
	StreamMetrics = "meteor.device.metrics"
	StreamStatus  = "meteor.device.status"
	StreamEvents  = "meteor.device.events"
)

// Subject prefixes (schema version 1).
const (
	SubjectTelemetry = "telemetry.v1"
	SubjectStatus    = "device.status.v1"
	SubjectEvents    = "device.events.v1"
)

// Rejection reasons, used as a bounded metric label.
const (
	ReasonInvalidTopic   = "invalid_topic"
	ReasonUnknownDevice  = "unknown_device"
	ReasonInvalidPayload = envelope.CodeInvalidPayload
	ReasonTooLarge       = envelope.CodePayloadTooLarge
	ReasonInvalidID      = envelope.CodeInvalidID
)

// ParseTopic accepts devices/<uuid>/{metrics,status,events}. The device id is
// returned in canonical lowercase form, which is also a valid NATS subject token.
func ParseTopic(topic string) (deviceID string, ch Channel, ok bool) {
	parts := strings.Split(topic, "/")
	if len(parts) != 3 || parts[0] != "devices" {
		return "", "", false
	}
	id, err := uuid.Parse(parts[1])
	if err != nil || len(parts[1]) != 36 {
		return "", "", false
	}
	switch Channel(parts[2]) {
	case ChannelMetrics, ChannelStatus, ChannelEvents:
		return id.String(), Channel(parts[2]), true
	}
	return "", "", false
}

// Subject is where an event of a channel is published. orgID and deviceID must be
// canonical UUIDs (they come from ParseTopic and the device directory), so they
// can never contain the "." "*" or ">" that would change the subject's meaning.
func Subject(ch Channel, orgID, deviceID string) string {
	prefix := SubjectTelemetry
	switch ch {
	case ChannelStatus:
		prefix = SubjectStatus
	case ChannelEvents:
		prefix = SubjectEvents
	}
	return prefix + "." + orgID + "." + deviceID
}

// validToken reports whether s is safe as one subject token.
func validToken(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '.' || c == '*' || c == '>' || c <= ' ' || c >= 0x7f {
			return false
		}
	}
	return true
}

// idNamespace makes content-derived event ids (UUIDv5) specific to MeteorCloud.
var idNamespace = uuid.MustParse("6f2b6a0e-5d0c-4d0e-9a52-6d6574656f72")

// Result tells ingestion what happened to one message.
type Result struct {
	Event *envelope.Event
	// TimeReplaced is true when the device's timestamp was outside the accepted
	// window (for example a Raspberry Pi without a real-time clock before NTP sync)
	// and the receive time was used instead.
	TimeReplaced bool
}

// Normalize validates a device payload and builds the event envelope. deviceID and
// orgID must come from the topic and the device directory.
//
// Payload rules: a JSON object of at most lim.MaxPayloadBytes. Optional fields
// "event_id" (or "id") give a device-chosen id; "timestamp" (or "time", RFC 3339)
// gives the measurement time. Everything else is the event data. When a device
// sends no id but a timestamp, the id is derived from the device, channel and the
// exact payload, so an MQTT redelivery of the same message gets the same id and is
// stored once; without either, a new time-ordered id is minted.
func Normalize(deviceID, orgID string, ch Channel, payload []byte, now time.Time, lim envelope.Limits) (*Result, *envelope.Rejection) {
	if !validToken(deviceID) || !validToken(orgID) {
		return nil, &envelope.Rejection{Code: ReasonInvalidTopic, Message: "device or organization id is not a valid subject token"}
	}
	if lim.MaxPayloadBytes > 0 && len(payload) > lim.MaxPayloadBytes {
		return nil, &envelope.Rejection{Code: ReasonTooLarge, Message: "payload is larger than the limit"}
	}
	trimmed := bytes.TrimSpace(payload)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return nil, &envelope.Rejection{Code: ReasonInvalidPayload, Message: "payload must be a JSON object"}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &fields); err != nil {
		return nil, &envelope.Rejection{Code: ReasonInvalidPayload, Message: "payload is not valid JSON"}
	}

	id := ""
	for _, key := range []string{"event_id", "id"} {
		raw, ok := fields[key]
		if !ok {
			continue
		}
		var s string
		if json.Unmarshal(raw, &s) != nil || !envelope.ValidID(s) {
			return nil, &envelope.Rejection{Code: ReasonInvalidID, Message: key + " must be 1-64 characters of A-Z a-z 0-9 . _ : -"}
		}
		id = s
		delete(fields, key)
		break
	}

	now = now.UTC()
	ts, hasTime, replaced := now, false, false
	for _, key := range []string{"timestamp", "time"} {
		raw, ok := fields[key]
		if !ok {
			continue
		}
		var s string
		if json.Unmarshal(raw, &s) == nil {
			if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
				hasTime = true
				t = t.UTC()
				if t.After(now.Add(lim.MaxFutureSkew)) || (lim.MaxAge > 0 && t.Before(now.Add(-lim.MaxAge))) {
					replaced = true
				} else {
					ts = t
				}
			}
		}
		delete(fields, key)
		break
	}

	if id == "" {
		if hasTime {
			id = uuid.NewSHA1(idNamespace, append([]byte(deviceID+"\x00"+string(ch)+"\x00"), trimmed...)).String()
		} else {
			id = envelope.NewID()
		}
	}

	data, err := json.Marshal(fields)
	if err != nil {
		return nil, &envelope.Rejection{Code: ReasonInvalidPayload, Message: "payload could not be re-encoded"}
	}
	ev := &envelope.Event{
		ID:             id,
		SchemaVersion:  envelope.SchemaVersion,
		OrganizationID: orgID,
		DeviceID:       deviceID,
		Time:           ts,
		ReceivedAt:     now,
		Data:           data,
	}
	switch ch {
	case ChannelMetrics:
		ev.Kind, ev.Stream, ev.Type = envelope.KindTelemetry, StreamMetrics, "device.metrics"
	case ChannelStatus:
		ev.Kind, ev.Stream, ev.Type = envelope.KindEvent, StreamStatus, "device.status"
	default:
		ev.Kind, ev.Stream, ev.Type = envelope.KindEvent, StreamEvents, "device.event"
		var t string
		if raw, ok := fields["type"]; ok && json.Unmarshal(raw, &t) == nil && t != "" && len(t) <= 128 {
			ev.Type = t
		}
	}
	return &Result{Event: ev, TimeReplaced: replaced}, nil
}
