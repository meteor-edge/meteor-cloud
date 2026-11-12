package bus

import (
	"testing"

	"meteorcloud/dataplane/internal/identity"
)

const org = "11111111-1111-1111-1111-111111111111"

func TestParseTopic(t *testing.T) {
	base := TopicPrefix + org
	good := map[string]Topic{
		base + "/apps/collector/devices/dev1/telemetry/energy.power": {OrgID: org, App: "collector", Device: "dev1", Kind: "telemetry", Stream: "energy.power", HasDevice: true},
		base + "/apps/collector/devices/cloud/events/energy.anomaly": {OrgID: org, App: "collector", Kind: "event", Stream: "energy.anomaly", HasDevice: true},
		base + "/apps/collector/devices/dev1/commands":               {OrgID: org, App: "collector", Device: "dev1", Kind: "commands", HasDevice: true},
		base + "/apps/collector/devices/dev1/commands/ack":           {OrgID: org, App: "collector", Device: "dev1", Kind: "ack", HasDevice: true},
		base + "/apps/collector/commands":                            {OrgID: org, App: "collector", Kind: "commands"},
		base + "/live/energy.power":                                  {OrgID: org, Kind: "live", Stream: "energy.power"},
	}
	for topic, want := range good {
		got, ok := ParseTopic(topic)
		if !ok || got != want {
			t.Errorf("%s: got %+v ok=%v", topic, got, ok)
		}
	}
	bad := []string{
		"devices/x/status", base + "/live/#", base + "/live/+", base + "/apps/+/devices/+/telemetry/x",
		base + "/apps/collector/devices/dev1/telemetry/Bad Stream", base + "/apps/collector/devices/dev1/unknown/x",
		base + "/apps//devices/dev1/telemetry/x", base + "/live/a/b", "meteor/v2/orgs/x/live/a", base,
	}
	for _, topic := range bad {
		if _, ok := ParseTopic(topic); ok {
			t.Errorf("%s should not parse", topic)
		}
	}
}

func TestAuthorize(t *testing.T) {
	id := &identity.Identity{OrganizationID: org, Application: "collector", DeviceID: "dev1",
		Permissions: identity.Permissions{Publish: []string{"energy.*"}, Subscribe: []string{"temperature.room"}, ReceiveCommands: true}}
	base := TopicPrefix + org
	allow := [][2]string{
		{"publish", base + "/apps/collector/devices/dev1/telemetry/energy.power"},
		{"publish", base + "/apps/collector/devices/dev1/events/energy.anomaly"},
		{"publish", base + "/apps/collector/devices/dev1/commands/ack"},
		{"subscribe", base + "/live/temperature.room"},
		{"subscribe", base + "/apps/collector/devices/dev1/commands"},
		{"subscribe", base + "/apps/collector/commands"},
	}
	for _, c := range allow {
		if !Authorize(id, c[0], c[1]) {
			t.Errorf("should allow %s %s", c[0], c[1])
		}
	}
	deny := [][2]string{
		{"publish", base + "/apps/collector/devices/dev2/telemetry/energy.power"},  // another device
		{"publish", base + "/apps/detector/devices/dev1/telemetry/energy.power"},   // another application
		{"publish", base + "/apps/collector/devices/cloud/telemetry/energy.power"}, // not a cloud instance
		{"publish", base + "/apps/collector/devices/dev1/telemetry/water.flow"},    // stream not granted
		{"publish", base + "/apps/collector/devices/dev1/telemetry/meteor.lifecycle"},
		{"publish", TopicPrefix + "22222222-2222-2222-2222-222222222222/apps/collector/devices/dev1/telemetry/energy.power"},
		{"publish", base + "/live/temperature.room"},                // only the platform publishes live
		{"publish", base + "/apps/collector/devices/dev1/commands"}, // applications cannot inject commands
		{"publish", base + "/apps/collector/commands"},
		{"subscribe", base + "/live/energy.power"}, // publish grant is not a subscribe grant
		{"subscribe", base + "/live/#"},
		{"subscribe", base + "/live/+"},
		{"subscribe", "#"},
		{"subscribe", "meteor/#"},
		{"subscribe", base + "/apps/detector/commands"},
		{"subscribe", base + "/apps/collector/devices/dev2/commands"},
		{"subscribe", base + "/apps/collector/devices/dev1/telemetry/energy.power"}, // raw ingress is not readable
		{"subscribe", "devices/dev1/commands"},                                      // legacy device topics
		{"publish", "devices/dev1/status"},
	}
	for _, c := range deny {
		if Authorize(id, c[0], c[1]) {
			t.Errorf("should deny %s %s", c[0], c[1])
		}
	}
	noCommands := *id
	noCommands.Permissions.ReceiveCommands = false
	if Authorize(&noCommands, "subscribe", base+"/apps/collector/commands") || Authorize(&noCommands, "publish", base+"/apps/collector/devices/dev1/commands/ack") {
		t.Error("commands need the receive_commands permission")
	}
}
