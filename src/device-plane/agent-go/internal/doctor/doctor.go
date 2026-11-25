// Package doctor turns what the agent knows about itself into a short list of
// checks, each with a verdict and a hint for the operator. `meteorcli doctor`
// prints the report, and the run_diagnostics action sends the same text to the
// console, so a device can be diagnosed without SSH.
//
// The package only judges facts; it never gathers them. The agent collects the
// facts (agent.Diagnose) because only it holds the live identity, MQTT session
// and module health. That keeps every check a plain function, easy to test.
// A report never contains a secret: facts carry states and errors, not tokens.
package doctor

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/identity"
)

// Level is how bad a check's result is.
type Level string

// Check levels, from fine to broken.
const (
	OK   Level = "ok"
	Warn Level = "warn"
	Fail Level = "fail"
)

// MaxText is the longest report text sent to the server (the contract's limit
// on an action result's output).
const MaxText = 16000

// lowDisk is the free space under which the state directory gets a warning:
// downloads and state writes need room.
const lowDisk = 100 << 20

// Check is one finding.
type Check struct {
	Name   string `json:"name"`
	Level  Level  `json:"level"`
	Detail string `json:"detail"`
	// Hint says what to do about it (empty when OK).
	Hint string `json:"hint,omitempty"`
}

// Report is the result of one diagnosis.
type Report struct {
	Version string    `json:"version"`
	Time    time.Time `json:"time"`
	Checks  []Check   `json:"checks"`
}

// Facts is what the agent knows when asked. Zero values mean "not known".
type Facts struct {
	Version string
	Now     time.Time
	// State is the identity's state (identity.State*).
	State  string
	Server string
	// ServerErr is the result of GET /api/v1/health; ServerChecked says it ran.
	ServerChecked bool
	ServerErr     error
	// LastHeartbeat is the last heartbeat the server accepted from this process.
	LastHeartbeat    time.Time
	HeartbeatSeconds int
	MQTTConfigured   bool
	// MQTT is the session; nil when the agent has no MQTT session.
	MQTT    *MQTTState
	Modules map[string]api.ModuleHealth
	// HelperErr is the result of a ping to the root helper; HelperChecked says it ran.
	HelperChecked bool
	HelperErr     error
	// DiskFree is the free space where the agent keeps state; DiskErr when unknown.
	DiskFree uint64
	DiskErr  error
}

// MQTTState is the part of the MQTT session the doctor looks at.
type MQTTState struct {
	Connected bool
	Broker    string
	LastError string
}

// Diagnose judges f.
func Diagnose(f Facts) Report {
	r := Report{Version: f.Version, Time: f.Now.UTC()}
	r.Checks = append(r.Checks, server(f), enrollment(f))
	if f.State == identity.StateEnrolled {
		r.Checks = append(r.Checks, heartbeat(f))
		if f.MQTTConfigured {
			r.Checks = append(r.Checks, mqtt(f))
		}
	}
	r.Checks = append(r.Checks, helperCheck(f), disk(f))
	r.Checks = append(r.Checks, modules(f.Modules)...)
	return r
}

// OK is true when no check failed (warnings are fine).
func (r Report) OK() bool {
	for _, c := range r.Checks {
		if c.Level == Fail {
			return false
		}
	}
	return true
}

// Text is the report for people: one line per check, hints indented below,
// cut to MaxText.
func (r Report) Text() string {
	var b strings.Builder
	fmt.Fprintf(&b, "meteorcli %s doctor, %s\n", r.Version, r.Time.Format(time.RFC3339))
	for _, c := range r.Checks {
		fmt.Fprintf(&b, "[%-4s] %-16s %s\n", c.Level, c.Name, c.Detail)
		if c.Hint != "" {
			fmt.Fprintf(&b, "       %-16s hint: %s\n", "", c.Hint)
		}
	}
	verdict := "No problems found."
	if !r.OK() {
		verdict = "Problems found: see the lines marked fail."
	}
	b.WriteString(verdict + "\n")
	text := b.String()
	if len(text) > MaxText {
		text = text[:MaxText]
	}
	return text
}

func server(f Facts) Check {
	c := Check{Name: "server"}
	switch {
	case f.Server == "":
		c.Level, c.Detail = Fail, "no server configured"
		c.Hint = "run 'sudo meteorcli config --domain <your MeteorCloud domain>'"
	case !f.ServerChecked:
		c.Level, c.Detail = Warn, f.Server+" (not checked)"
	case f.ServerErr != nil:
		c.Level, c.Detail = Fail, fmt.Sprintf("%s does not answer: %v", f.Server, f.ServerErr)
		c.Hint = "check the network, DNS and the server address ('meteorcli status')"
	default:
		c.Level, c.Detail = OK, f.Server+" answers"
	}
	return c
}

func enrollment(f Facts) Check {
	c := Check{Name: "enrollment", Detail: f.State}
	switch f.State {
	case identity.StateEnrolled:
		c.Level = OK
	case identity.StatePending:
		c.Level, c.Hint = Warn, "approve the request in the console (Fleet, Devices, pending requests)"
	case identity.StateWaiting, identity.StateNotConfigured:
		c.Level, c.Hint = Fail, "enroll: 'meteorcli register --token …' or 'meteorcli request-token'"
	case identity.StateRejected:
		c.Level, c.Hint = Fail, "the server refused the device token; enroll the device again"
	default:
		c.Level = Warn
	}
	return c
}

func heartbeat(f Facts) Check {
	c := Check{Name: "heartbeat"}
	if f.LastHeartbeat.IsZero() {
		c.Level, c.Detail = Warn, "no heartbeat accepted since the agent started"
		c.Hint = "wait a minute; if it stays, look at 'meteorcli logs --source meteorcli'"
		return c
	}
	age := f.Now.Sub(f.LastHeartbeat).Round(time.Second)
	every := time.Duration(max(f.HeartbeatSeconds, 60)) * time.Second
	c.Detail = fmt.Sprintf("last accepted %s ago", age)
	if age > 3*every {
		c.Level, c.Hint = Warn, "heartbeats are late; check the network and the agent logs"
	} else {
		c.Level = OK
	}
	return c
}

func mqtt(f Facts) Check {
	c := Check{Name: "mqtt"}
	switch {
	case f.MQTT == nil:
		c.Level, c.Detail = Warn, "configured, no session yet"
	case f.MQTT.Connected:
		c.Level, c.Detail = OK, "connected to "+f.MQTT.Broker
	default:
		c.Level, c.Detail = Fail, "disconnected"
		if f.MQTT.LastError != "" {
			c.Detail += ": " + f.MQTT.LastError
		}
		c.Hint = "check that the broker port is reachable from this network"
	}
	return c
}

func helperCheck(f Facts) Check {
	c := Check{Name: "root helper"}
	switch {
	case !f.HelperChecked:
		c.Level, c.Detail = Warn, "not checked"
	case f.HelperErr != nil:
		c.Level, c.Detail = Fail, fmt.Sprintf("does not answer: %v", f.HelperErr)
		c.Hint = "deploys, actions and Wi-Fi need it: 'sudo systemctl status meteorcli-helper.socket'"
	default:
		c.Level, c.Detail = OK, "answers"
	}
	return c
}

func disk(f Facts) Check {
	c := Check{Name: "disk"}
	switch {
	case f.DiskErr != nil:
		c.Level, c.Detail = Warn, fmt.Sprintf("free space unknown: %v", f.DiskErr)
	case f.DiskFree < lowDisk:
		c.Level, c.Detail = Fail, fmt.Sprintf("%d MB free for agent state", f.DiskFree>>20)
		c.Hint = "free some space: downloads and state files need room"
	default:
		c.Level, c.Detail = OK, fmt.Sprintf("%d MB free", f.DiskFree>>20)
	}
	return c
}

// modules is one check per module, failed and parked first.
func modules(m map[string]api.ModuleHealth) []Check {
	names := make([]string, 0, len(m))
	for n := range m {
		names = append(names, n)
	}
	rank := func(s string) int {
		switch s {
		case "parked":
			return 0
		case "failed":
			return 1
		}
		return 2
	}
	sort.Slice(names, func(i, j int) bool {
		a, b := rank(m[names[i]].State), rank(m[names[j]].State)
		if a != b {
			return a < b
		}
		return names[i] < names[j]
	})
	out := make([]Check, 0, len(names))
	for _, n := range names {
		h := m[n]
		c := Check{Name: "module " + n, Detail: h.State}
		if h.Restarts > 0 {
			c.Detail += fmt.Sprintf(", %d restarts", h.Restarts)
		}
		if h.LastError != nil && *h.LastError != "" && h.State != "running" {
			c.Detail += ": " + *h.LastError
		}
		switch h.State {
		case "parked":
			c.Level, c.Hint = Fail, fmt.Sprintf("it kept crashing and was stopped; fix the cause, then 'meteorcli module restart %s' (or Restart in the console)", n)
		case "failed":
			c.Level, c.Hint = Warn, "it is restarted automatically with backoff"
		default:
			c.Level = OK
		}
		out = append(out, c)
	}
	return out
}
