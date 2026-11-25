// Package bus is the agent's one MQTT session with the broker and the router in
// front of it. Modules publish through Router.Publish and read commands from an
// Inbox; none of them sees the connection. Every topic is
// devices/{device_id}/<suffix>, and only the suffixes of the contract are
// allowed (contracts/device-agent-v1.md, "MQTT"): the broker would refuse the
// others anyway, and a typo should fail here, loudly.
package bus

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// Timestamp formats t as the contract's MQTT timestamps (UTC, seconds, "Z").
func Timestamp(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05Z") }

// Topic suffixes of the contract, under devices/{device_id}/.
const (
	// Commands is the only topic a device subscribes to.
	Commands = "commands"
	// CommandsResult answers a command (ping).
	CommandsResult = "commands/result"
	// Status is online/offline, retained.
	Status = "status"
	// Metrics are published every 60 s.
	Metrics = "metrics"
	// Events are app lifecycle changes and journal warnings.
	Events = "events"
)

// publishable and subscribable list the suffixes a device may use.
var (
	publishable  = map[string]bool{Status: true, Metrics: true, Events: true, CommandsResult: true}
	subscribable = map[string]bool{Commands: true}
)

// Topic returns the full topic of suffix for deviceID.
func Topic(deviceID, suffix string) string { return "devices/" + deviceID + "/" + suffix }

// ResolveTopic turns what a user typed (a suffix like "status", or a full
// devices/<id>/status) into an allowed suffix, like the Python CLI did.
// publish chooses the publish or the subscribe list; empty means the default
// (status for publish, commands for subscribe). Wildcards are refused.
func ResolveTopic(deviceID, raw string, publish bool) (string, error) {
	allowed, def := subscribable, Commands
	if publish {
		allowed, def = publishable, Status
	}
	raw = strings.TrimSpace(raw)
	if raw == "" {
		raw = def
	}
	for _, part := range strings.Split(raw, "/") {
		if part == "+" || part == "#" {
			return "", fmt.Errorf("MQTT wildcards are not allowed")
		}
	}
	prefix := "devices/" + deviceID + "/"
	suffix := raw
	if strings.HasPrefix(raw, "devices/") {
		if !strings.HasPrefix(raw, prefix) {
			return "", fmt.Errorf("topic must be under %s", strings.TrimSuffix(prefix, "/"))
		}
		suffix = strings.TrimPrefix(raw, prefix)
	}
	if !allowed[suffix] {
		return "", fmt.Errorf("topic must be one of: %s", strings.Join(names(allowed), ", "))
	}
	return suffix, nil
}

func names(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// loopback hosts are replaced by the API host (see BrokerHost).
var loopback = map[string]bool{"localhost": true, "127.0.0.1": true, "::1": true, "[::1]": true}

// BrokerHost returns the host to connect to. A server that hands out a
// loopback broker address (a laptop stack) is reached on the API's host
// instead, so a device on the LAN finds it; the Python agent did the same.
// apiHost is the host of the configured API URL ("" when unknown).
func BrokerHost(stored, apiHost string) string {
	host := strings.TrimSpace(stored)
	if host == "" {
		host = "localhost"
	}
	if !loopback[strings.ToLower(host)] || apiHost == "" || loopback[strings.ToLower(apiHost)] {
		return host
	}
	return apiHost
}
