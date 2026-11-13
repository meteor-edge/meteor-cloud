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
//
// In a topic the dots of a stream name become levels ("energy.power" is
// "energy/power"), so a stream permission pattern is an ordinary MQTT topic filter:
// "energy.*" is "energy/+" and "energy.**" is "energy/+/#" (one or more levels below).
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

// StreamLevels turns a stream name or pattern into topic levels.
func StreamLevels(pattern string) string {
	parts := strings.Split(pattern, ".")
	for i, p := range parts {
		switch p {
		case "*":
			parts[i] = "+"
		case "**":
			// "**" is one or more words. MQTT "#" alone also matches the parent
			// level, so one level is required first.
			parts[i] = "+/#"
		}
	}
	return strings.Join(parts, "/")
}

func streamFromLevels(levels []string) (string, bool) {
	if len(levels) == 0 {
		return "", false
	}
	name := strings.Join(levels, ".")
	return name, streams.ValidateName(name) == nil
}

// LiveTopic is where stored events of a stream are published.
func LiveTopic(orgID, stream string) string {
	return TopicPrefix + orgID + "/live/" + StreamLevels(stream)
}

// IngressTopic is where an application instance publishes messages of a kind
// ("telemetry", "events" or "inference") for a stream name or pattern.
func IngressTopic(orgID, app, deviceID, kindSegment, stream string) string {
	return TopicPrefix + orgID + "/apps/" + app + "/devices/" + deviceSegment(deviceID) + "/" + kindSegment + "/" + StreamLevels(stream)
}

// AckTopic is where an application instance acknowledges commands.
func AckTopic(orgID, app, deviceID string) string {
	return TopicPrefix + orgID + "/apps/" + app + "/devices/" + deviceSegment(deviceID) + "/commands/ack"
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
	case len(parts) >= 3 && parts[1] == "live":
		t.Kind = "live"
		var ok bool
		t.Stream, ok = streamFromLevels(parts[2:])
		return t, ok
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
		case len(rest) >= 2:
			kind, ok := ingressKinds[rest[0]]
			if !ok {
				return Topic{}, false
			}
			t.Kind = kind
			t.Stream, ok = streamFromLevels(rest[1:])
			return t, ok
		}
	}
	return Topic{}, false
}

// ACL is the complete list of topic filters one application instance may use. It is
// embedded in the short-lived MQTT token the broker verifies, so the broker enforces
// it without calling anyone. Anything not listed is denied.
type ACL struct {
	Publish   []string `json:"pub"`
	Subscribe []string `json:"sub"`
}

// BuildACL derives the per-identity topic ACL from the application's permissions.
func BuildACL(id *identity.Identity) ACL {
	acl := ACL{Publish: []string{}, Subscribe: []string{}}
	for _, pattern := range id.Permissions.Publish {
		if streams.ValidatePattern(pattern) != nil || streams.IsReserved(strings.Split(pattern, ".")[0]) {
			continue
		}
		for _, kind := range []string{"telemetry", "events", "inference"} {
			acl.Publish = append(acl.Publish, IngressTopic(id.OrganizationID, id.Application, id.DeviceID, kind, pattern))
		}
	}
	for _, pattern := range id.Permissions.Subscribe {
		if streams.ValidatePattern(pattern) != nil {
			continue
		}
		acl.Subscribe = append(acl.Subscribe, LiveTopic(id.OrganizationID, pattern))
	}
	if id.Permissions.ReceiveCommands {
		acl.Publish = append(acl.Publish, AckTopic(id.OrganizationID, id.Application, id.DeviceID))
		acl.Subscribe = append(acl.Subscribe, CommandTopic(id.OrganizationID, id.Application, ""))
		if id.DeviceID != "" {
			acl.Subscribe = append(acl.Subscribe, CommandTopic(id.OrganizationID, id.Application, id.DeviceID))
		}
	}
	return acl
}

// FilterMatches reports whether an MQTT topic filter matches a topic or, for
// subscriptions, covers a requested filter.
func FilterMatches(filter, topic string) bool {
	fp := strings.Split(filter, "/")
	tp := strings.Split(topic, "/")
	for i, f := range fp {
		if f == "#" {
			return true
		}
		if i >= len(tp) {
			return false
		}
		if tp[i] == "#" || (tp[i] == "+" && f != "+") {
			return false // the request is wider than the grant
		}
		if f != "+" && f != tp[i] {
			return false
		}
	}
	return len(fp) == len(tp)
}

// Allows reports whether the ACL permits the action on the topic (tests and tools;
// the broker does the real enforcement from the same lists).
func (a ACL) Allows(action, topic string) bool {
	list := a.Publish
	if action == "subscribe" {
		list = a.Subscribe
	} else if strings.ContainsAny(topic, "+#") {
		return false
	}
	for _, f := range list {
		if FilterMatches(f, topic) {
			return true
		}
	}
	return false
}
