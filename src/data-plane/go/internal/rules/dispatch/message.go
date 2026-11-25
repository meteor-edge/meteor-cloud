package dispatch

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"meteorcloud/dataplane/internal/rules/engine"
)

// metricNames are the readable names of the agent's metrics, for messages to people.
var metricNames = map[string][2]string{ // field -> name, unit
	"cpu_percent":     {"CPU", "%"},
	"memory_percent":  {"Memory", "%"},
	"disk_percent":    {"Disk", "%"},
	"temperature_c":   {"Temperature", " °C"},
	"load_1m":         {"Load (1 min)", ""},
	"load_5m":         {"Load (5 min)", ""},
	"load_15m":        {"Load (15 min)", ""},
	"wifi_signal_dbm": {"Wi-Fi signal", " dBm"},
	"net_rx_bps":      {"Network in", " B/s"},
	"net_tx_bps":      {"Network out", " B/s"},
	"uptime_seconds":  {"Uptime", " s"},
	"agent_errors":    {"Agent errors", ""},
}

func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

func headline(d *Delivery, p *engine.ActionPayload) string {
	state := "ALERT"
	if p.Transition.Kind == engine.TransitionResolved {
		state = "RESOLVED"
	}
	return fmt.Sprintf("[%s] %s: %s on %s", state, strings.ToUpper(p.Severity), oneLine(p.RuleName), deviceName(d, p))
}

func deviceName(d *Delivery, p *engine.ActionPayload) string {
	if len(d.Group) > 1 {
		return strconv.Itoa(len(d.Group)) + " devices"
	}
	if n := oneLine(d.DeviceName); n != "" {
		return n
	}
	return "device " + p.DeviceID
}

// readings is one "Name: value" per reading, in a stable order.
func readings(values map[string]float64) []string {
	keys := make([]string, 0, len(values))
	for k := range values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		name, unit := k, ""
		if m, ok := metricNames[k]; ok {
			name, unit = m[0], m[1]
		}
		out = append(out, name+": "+strconv.FormatFloat(values[k], 'g', 6, 64)+unit)
	}
	return out
}

// messageText is the plain-text body of email and chat messages.
func messageText(d *Delivery, p *engine.ActionPayload) string {
	var b strings.Builder
	row := func(k, v string) {
		if v != "" {
			fmt.Fprintf(&b, "%s: %s\n", k, v)
		}
	}
	row("Organization", oneLine(d.OrganizationName))
	if len(d.Group) > 1 {
		row("Devices", strconv.Itoa(len(d.Group)))
	} else {
		row("Device", deviceName(d, p))
	}
	row("Rule", oneLine(p.RuleName))
	row("Severity", p.Severity)
	if len(d.Group) < 2 {
		for _, r := range readings(p.Transition.Values) {
			b.WriteString(r + "\n")
		}
	}
	if p.Transition.Kind == engine.TransitionResolved && p.Transition.Reason != engine.ReasonCleared {
		row("Reason", strings.ReplaceAll(p.Transition.Reason, "_", " "))
	}
	row("Time", p.Transition.At.UTC().Format("2006-01-02 15:04:05 UTC"))
	if len(d.Group) > 1 {
		b.WriteString("\n")
		for i, line := range d.Group {
			if i == maxListed {
				fmt.Fprintf(&b, "… and %d more\n", len(d.Group)-maxListed)
				break
			}
			b.WriteString("- " + line + "\n")
		}
	}
	if d.Link != "" {
		b.WriteString("\n" + d.Link + "\n")
	}
	return b.String()
}
