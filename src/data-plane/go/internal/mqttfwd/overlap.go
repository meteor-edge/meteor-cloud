package mqttfwd

import "strings"

// A watch (the console's live MQTT view) can overlap the device topics this
// forwarder already takes through the shared group. The broker delivers a
// message once per matching subscription, so without care every overlapping
// message would be ingested twice: applied twice and shown twice in the
// console.
//
//   - When the broker supports subscription identifiers and a shared group is
//     used, every watch is subscribed with watchSubID, and a watch copy of a
//     live (not retained) device message is acknowledged and dropped: the
//     shared subscription delivers it. The retained status a new watch gets is
//     kept (the shared subscription never sends retained messages; the console
//     shows it as the current state). A shared subscription's copy is always
//     sent on its own, so a watch copy carries only watch ids; paho keeps one
//     id per message, which is enough then.
//   - Otherwise a watch that DefaultTopics fully cover (devices/<id>/status,
//     ...) gets no subscription of its own, and one that only partly overlaps
//     (devices/<id>/#) still brings duplicates of the overlapping part.
//
// The retained status replay after a connect (retained.go) is a plain
// devices/+/status subscription too; it is marked with replaySubID, and its
// copies of live (not retained) status messages are dropped the same way.

// Subscription identifiers (MQTT 5) of the plain subscriptions.
const (
	watchSubID  = 1
	replaySubID = 2
)

// coveredByDefault reports whether every topic the filter can match is also
// matched by one of DefaultTopics.
func coveredByDefault(filter string) bool {
	for _, d := range DefaultTopics {
		if filterCovers(d, filter) {
			return true
		}
	}
	return false
}

// matchesDefault reports whether a concrete topic is one of the device topics.
func matchesDefault(topic string) bool {
	for _, d := range DefaultTopics {
		if topicMatches(d, topic) {
			return true
		}
	}
	return false
}

// filterCovers reports whether every topic matched by inner is matched by outer.
func filterCovers(outer, inner string) bool {
	o, in := strings.Split(outer, "/"), strings.Split(inner, "/")
	for i, level := range o {
		if level == "#" {
			return true
		}
		if i >= len(in) || in[i] == "#" {
			return false
		}
		if level != "+" && level != in[i] {
			return false // a literal covers only the same literal, never a wildcard
		}
	}
	return len(o) == len(in)
}

// topicMatches reports whether a concrete topic matches the filter.
func topicMatches(filter, topic string) bool {
	if strings.HasPrefix(topic, "$") && !strings.HasPrefix(filter, "$") {
		return false // wildcards never match $-topics (MQTT 4.7.2)
	}
	return filterCovers(filter, topic)
}
