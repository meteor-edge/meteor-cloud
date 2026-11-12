package bus

import (
	"strings"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/streams"
)

// MQTT topic hierarchy, version 1 (contracts/data-plane-v1.md):
//
//	meteor/v1/orgs/{org}/apps/{app}/devices/{device|cloud}/telemetry/{stream}   client -> platform
//	meteor/v1/orgs/{org}/apps/{app}/devices/{device|cloud}/events/{stream}      client -> platform
//	meteor/v1/orgs/{org}/apps/{app}/devices/{device|cloud}/inference/{stream}   client -> platform
//	meteor/v1/orgs/{org}/apps/{app}/devices/{device|cloud}/commands/ack         client -> platform
//	meteor/v1/orgs/{org}/apps/{app}/devices/{device|cloud}/commands             platform -> client
//	meteor/v1/orgs/{org}/apps/{app}/commands                                    platform -> client
//	meteor/v1/orgs/{org}/live/{stream}                                          platform -> client
const (
	TopicPrefix = "meteor/v1/orgs/"
	// CloudDevice stands in for the device segment of cloud-hosted instances.
	CloudDevice = "cloud"
	// AppUsernamePrefix marks application MQTT usernames: "app_<credential id>".
	AppUsernamePrefix = "app_"
)

var ingressKinds = map[string]string{
	"telemetry": envelope.KindTelemetry,
	"events":    envelope.KindEvent,
	"inference": envelope.KindInference,
}

func deviceSegment(deviceID string) string {
	if deviceID == "" {
		return CloudDevice
	}
	return deviceID
}

// LiveTopic is where stored events of a stream are published.
func LiveTopic(orgID, stream string) string {
	return TopicPrefix + orgID + "/live/" + stream
}

// CommandTopic is where a command for the target is published.
func CommandTopic(orgID, app, deviceID string) string {
	if deviceID == "" {
		return TopicPrefix + orgID + "/apps/" + app + "/commands"
	}
	return TopicPrefix + orgID + "/apps/" + app + "/devices/" + deviceID + "/commands"
}

// Topic is a parsed v1 topic.
type Topic struct {
	OrgID  string
	App    string
	Device string // "" for cloud instances
	// Kind is "telemetry", "event", "inference", "ack", "commands" or "live".
	Kind   string
	Stream string
	// HasDevice tells an app-wide commands topic from a device-scoped one.
	HasDevice bool
}

// ParseTopic parses a concrete topic (no wildcards). ok is false for anything that
// is not exactly a v1 topic.
func ParseTopic(topic string) (Topic, bool) {
	if !strings.HasPrefix(topic, TopicPrefix) || strings.ContainsAny(topic, "+#") {
		return Topic{}, false
	}
	parts := strings.Split(topic[len(TopicPrefix):], "/")
	for _, p := range parts {
		if p == "" {
			return Topic{}, false
		}
	}
	t := Topic{OrgID: parts[0]}
	switch {
	case len(parts) == 3 && parts[1] == "live":
		t.Kind, t.Stream = "live", parts[2]
		return t, streams.ValidateName(t.Stream) == nil
	case len(parts) == 4 && parts[1] == "apps" && parts[3] == "commands":
		t.App, t.Kind = parts[2], "commands"
		return t, true
	case len(parts) >= 6 && parts[1] == "apps" && parts[3] == "devices":
		t.App, t.HasDevice = parts[2], true
		if parts[4] != CloudDevice {
			t.Device = parts[4]
		}
		rest := parts[5:]
		switch {
		case len(rest) == 1 && rest[0] == "commands":
			t.Kind = "commands"
			return t, true
		case len(rest) == 2 && rest[0] == "commands" && rest[1] == "ack":
			t.Kind = "ack"
			return t, true
		case len(rest) == 2:
			kind, ok := ingressKinds[rest[0]]
			if !ok {
				return Topic{}, false
			}
			t.Kind, t.Stream = kind, rest[1]
			return t, streams.ValidateName(t.Stream) == nil
		}
	}
	return Topic{}, false
}

// Authorize is the per-identity MQTT ACL. Anything not explicitly allowed is denied;
// wildcard filters never match because ParseTopic rejects them.
func Authorize(id *identity.Identity, action, topic string) bool {
	t, ok := ParseTopic(topic)
	if !ok || t.OrgID != id.OrganizationID {
		return false
	}
	own := t.App == id.Application && (!t.HasDevice || t.Device == id.DeviceID)
	switch action {
	case "publish":
		switch t.Kind {
		case envelope.KindTelemetry, envelope.KindEvent, envelope.KindInference:
			return own && id.CanPublish(t.Stream)
		case "ack":
			return own && id.Permissions.ReceiveCommands
		}
	case "subscribe":
		switch t.Kind {
		case "live":
			return id.CanSubscribe(t.Stream)
		case "commands":
			return own && id.Permissions.ReceiveCommands
		}
	}
	return false
}
