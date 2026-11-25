// Package dsl defines the versioned JSON rule definition (contracts/rules-v1.md,
// contracts/rules-v1.schema.json) and validates it.
//
// A rule is data, never code: every condition is a typed node from a closed set,
// compiled once by package eval. Nothing in a rule can execute scripts, reach the
// network or read another tenant's data.
package dsl

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"regexp"
	"slices"
	"strings"
	"time"
)

// SchemaVersion is the only rule definition version this engine accepts.
const SchemaVersion = 1

// Severity levels.
const (
	SeverityInfo     = "info"
	SeverityWarning  = "warning"
	SeverityCritical = "critical"
)

// Scope types.
const (
	ScopeDevice       = "device"
	ScopeGroup        = "group"
	ScopeDeviceType   = "device_type"
	ScopeOrganization = "organization"
	ScopePlatform     = "platform"
)

// Node types.
const (
	NodeComparison  = "comparison"
	NodeAll         = "all"
	NodeAny         = "any"
	NodeNot         = "not"
	NodeDuration    = "duration"
	NodeCountWithin = "count_within"
	NodeConsecutive = "consecutive"
	NodeAggregate   = "aggregate"
	NodeAbsence     = "absence"
)

// Comparison operators.
const (
	OpEq        = "eq"
	OpNe        = "ne"
	OpGt        = "gt"
	OpGte       = "gte"
	OpLt        = "lt"
	OpLte       = "lte"
	OpBetween   = "between"
	OpIn        = "in"
	OpNotIn     = "not_in"
	OpExists    = "exists"
	OpNotExists = "not_exists"
)

// Aggregate functions.
const (
	FnAvg   = "avg"
	FnMin   = "min"
	FnMax   = "max"
	FnSum   = "sum"
	FnCount = "count"
	FnDelta = "delta"
	FnRate  = "rate"
)

// Action types.
const (
	ActionAlert         = "alert"
	ActionWebhook       = "webhook"
	ActionCommand       = "command"
	ActionNotification  = "notification"
	ActionPlatformEvent = "platform_event"
)

// Transitions an action can run on.
const (
	OnFiring   = "firing"
	OnResolved = "resolved"
)

// Alerting modes.
const (
	ModeSingle   = "single"
	ModePerEvent = "per_event"
)

// DefaultStream is what a rule reads when it names no source.
const DefaultStream = "meteor.device.metrics"

// Limits bound what one rule may ask of the engine.
type Limits struct {
	MaxDepth     int
	MaxNodes     int
	MinWindow    time.Duration
	MaxWindow    time.Duration
	MaxActions   int
	MaxListItems int
	MaxDevices   int
}

// DefaultLimits are the limits documented in contracts/rules-v1.md.
func DefaultLimits() Limits {
	return Limits{MaxDepth: 8, MaxNodes: 64, MinWindow: time.Second, MaxWindow: 24 * time.Hour,
		MaxActions: 10, MaxListItems: 100, MaxDevices: 1000}
}

// Duration is a JSON duration string such as "90s", "5m" or "1h30m".
type Duration time.Duration

// durationForm is the one spelling the engine, the API and the schema accept. Go alone
// would also take "0", a sign and ".5s".
var durationForm = regexp.MustCompile(`^([0-9]+(\.[0-9]+)?(ns|us|µs|ms|s|m|h))+$`)

func (d Duration) D() time.Duration { return time.Duration(d) }

func (d Duration) MarshalJSON() ([]byte, error) { return json.Marshal(time.Duration(d).String()) }

func (d *Duration) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return fmt.Errorf("duration must be a string like \"5m\"")
	}
	v, err := time.ParseDuration(s)
	if err != nil || !durationForm.MatchString(s) {
		return fmt.Errorf("invalid duration %q", s)
	}
	*d = Duration(v)
	return nil
}

// Rule is a complete rule definition.
type Rule struct {
	SchemaVersion int       `json:"schema_version"`
	Name          string    `json:"name"`
	Description   string    `json:"description,omitempty"`
	Enabled       *bool     `json:"enabled,omitempty"`
	Severity      string    `json:"severity,omitempty"`
	Scope         Scope     `json:"scope"`
	Source        *Source   `json:"source,omitempty"`
	Condition     *Node     `json:"condition"`
	Resolve       *Node     `json:"resolve,omitempty"`
	Cooldown      Duration  `json:"cooldown,omitempty"`
	Alerting      *Alerting `json:"alerting,omitempty"`
	Actions       []Action  `json:"actions,omitempty"`
	LateTolerance *Duration `json:"late_tolerance,omitempty"`
}

// Scope selects the devices a rule applies to.
type Scope struct {
	Type         string            `json:"type"`
	DeviceIDs    []string          `json:"device_ids,omitempty"`
	GroupID      string            `json:"group_id,omitempty"`
	DeviceTypeID string            `json:"device_type_id,omitempty"`
	Labels       map[string]string `json:"labels,omitempty"`

	// keys are the JSON keys the scope was written with (nil when built in code).
	keys map[string]bool
}

// has reports whether the scope carries a key, whatever its value.
func (s Scope) has(name string) bool {
	if s.keys != nil {
		return s.keys[name]
	}
	switch name {
	case "device_ids":
		return len(s.DeviceIDs) > 0
	case "group_id":
		return s.GroupID != ""
	case "device_type_id":
		return s.DeviceTypeID != ""
	}
	return false
}

// Source names the event streams that feed a rule.
type Source struct {
	Streams []string `json:"streams,omitempty"`
}

// Alerting controls how many alerts a rule may open per device.
type Alerting struct {
	Mode      string `json:"mode,omitempty"`
	MaxActive *int   `json:"max_active,omitempty"`
}

// Node is one condition node. Which fields apply depends on Type.
type Node struct {
	Type string `json:"type"`

	// comparison
	Field    string            `json:"field,omitempty"`
	Operator string            `json:"operator,omitempty"`
	Value    json.RawMessage   `json:"value,omitempty"`
	Values   []json.RawMessage `json:"values,omitempty"`
	Min      *float64          `json:"min,omitempty"`
	Max      *float64          `json:"max,omitempty"`

	// all / any / not / duration / count_within / consecutive
	Conditions []*Node `json:"conditions,omitempty"`
	Condition  *Node   `json:"condition,omitempty"`

	// duration / absence / count_within / aggregate
	Duration Duration `json:"duration,omitempty"`
	Window   Duration `json:"window,omitempty"`
	MinCount int      `json:"min_count,omitempty"`
	Count    int      `json:"count,omitempty"`
	Function string   `json:"function,omitempty"`
	Streams  []string `json:"streams,omitempty"`

	// keys are the JSON keys the node was written with (nil when built in code), so
	// validation sees a misplaced key even when its value is zero.
	keys map[string]bool
}

// Action is something to do on a transition. Config is action specific and checked
// by the dispatcher that owns that action type.
type Action struct {
	Type          string          `json:"type"`
	On            []string        `json:"on,omitempty"`
	Severity      string          `json:"severity,omitempty"`
	IntegrationID string          `json:"integration_id,omitempty"`
	Command       string          `json:"command,omitempty"`
	Params        json.RawMessage `json:"params,omitempty"`
	Timeout       Duration        `json:"timeout,omitempty"`
}

// IsEnabled reports the enabled flag (default true).
func (r *Rule) IsEnabled() bool { return r.Enabled == nil || *r.Enabled }

// SourceStreams returns the streams that feed the rule's conditions.
func (r *Rule) SourceStreams() []string {
	if r.Source == nil || len(r.Source.Streams) == 0 {
		return []string{DefaultStream}
	}
	return r.Source.Streams
}

// Streams returns every stream the rule reads: its sources first, then the streams
// named only by absence nodes.
func (r *Rule) Streams() []string {
	out := slices.Clone(r.SourceStreams())
	var walk func(n *Node)
	walk = func(n *Node) {
		if n == nil {
			return
		}
		if n.Type == NodeAbsence {
			for _, s := range n.Streams {
				if !slices.Contains(out, s) {
					out = append(out, s)
				}
			}
		}
		for _, c := range n.Conditions {
			walk(c)
		}
		walk(n.Condition)
	}
	walk(r.Condition)
	return out
}

// EffectiveSeverity returns the severity with its default.
func (r *Rule) EffectiveSeverity() string {
	if r.Severity == "" {
		return SeverityWarning
	}
	return r.Severity
}

// LateBy returns the late tolerance with its default.
func (r *Rule) LateBy() time.Duration {
	if r.LateTolerance == nil {
		return time.Minute
	}
	return r.LateTolerance.D()
}

// Mode returns the alerting mode and the cap on active alerts per device.
func (r *Rule) Mode() (string, int) {
	if r.Alerting == nil || r.Alerting.Mode == "" || r.Alerting.Mode == ModeSingle {
		return ModeSingle, 1
	}
	if r.Alerting.MaxActive == nil {
		return ModePerEvent, 10
	}
	return ModePerEvent, *r.Alerting.MaxActive
}

// EffectiveActions returns the actions with defaults applied: an alert action when
// none is configured, and each action's "on" list filled.
func (r *Rule) EffectiveActions() []Action {
	acts := r.Actions
	if len(acts) == 0 {
		acts = []Action{{Type: ActionAlert}}
	}
	out := make([]Action, len(acts))
	for i, a := range acts {
		if len(a.On) == 0 || a.Type == ActionAlert {
			if a.Type == ActionAlert {
				a.On = []string{OnFiring, OnResolved}
			} else {
				a.On = []string{OnFiring}
			}
		}
		out[i] = a
	}
	return out
}

// Parse decodes a rule strictly: unknown fields are errors, so a typo never turns
// into a silently ignored setting.
func Parse(raw []byte) (*Rule, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var r Rule
	if err := dec.Decode(&r); err != nil {
		return nil, &ValidationError{Problems: []Problem{{Path: "", Message: cleanJSONError(err)}}}
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, &ValidationError{Problems: []Problem{{Path: "", Message: "trailing data after the rule"}}}
	}
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, &ValidationError{Problems: []Problem{{Path: "", Message: cleanJSONError(err)}}}
	}
	if problems := checkKeys(&r, doc); len(problems) > 0 {
		return nil, &ValidationError{Problems: problems}
	}
	return &r, nil
}

// The exact keys of each object. encoding/json matches keys without case, so a strict
// decode alone would accept "Name", which the API and the schema refuse.
var (
	topKeys = keySet("schema_version", "name", "description", "enabled", "severity", "scope", "source",
		"condition", "resolve", "cooldown", "alerting", "actions", "late_tolerance")
	scopeKeys    = keySet("type", "device_ids", "group_id", "device_type_id", "labels")
	sourceKeys   = keySet("streams")
	alertingKeys = keySet("mode", "max_active")
	actionKeys   = keySet("type", "on", "severity", "integration_id", "command", "params", "timeout")
	nodeKeys     = keySet("type", "field", "operator", "value", "values", "min", "max", "conditions",
		"condition", "duration", "window", "min_count", "count", "function", "streams")
)

func keySet(names ...string) map[string]bool {
	out := make(map[string]bool, len(names))
	for _, n := range names {
		out[n] = true
	}
	return out
}

// checkKeys refuses misspelled keys and records which keys the scope and the nodes were
// written with. The rule has already been decoded, so the shapes are known to fit.
func checkKeys(r *Rule, doc map[string]json.RawMessage) []Problem {
	var problems []Problem
	check := func(path string, raw json.RawMessage, allowed map[string]bool) map[string]json.RawMessage {
		var obj map[string]json.RawMessage
		_ = json.Unmarshal(raw, &obj)
		for _, k := range slices.Sorted(maps.Keys(obj)) {
			if !allowed[k] {
				problems = append(problems, Problem{Path: strings.TrimPrefix(path+"."+k, "."), Message: "unknown field"})
			}
		}
		return obj
	}
	for _, k := range slices.Sorted(maps.Keys(doc)) {
		if !topKeys[k] {
			problems = append(problems, Problem{Path: k, Message: "unknown field"})
		}
	}
	if raw, ok := doc["enabled"]; ok && r.Enabled == nil {
		problems = append(problems, Problem{Path: "enabled", Message: fmt.Sprintf("must be true or false, not %s", raw)})
	}
	r.Scope.keys = map[string]bool{}
	for k := range check("scope", doc["scope"], scopeKeys) {
		r.Scope.keys[k] = true
	}
	check("source", doc["source"], sourceKeys)
	check("alerting", doc["alerting"], alertingKeys)
	var actions []json.RawMessage
	_ = json.Unmarshal(doc["actions"], &actions)
	for i, a := range actions {
		check(fmt.Sprintf("actions[%d]", i), a, actionKeys)
	}
	var node func(path string, n *Node, raw json.RawMessage)
	node = func(path string, n *Node, raw json.RawMessage) {
		if n == nil {
			return
		}
		obj := check(path, raw, nodeKeys)
		n.keys = make(map[string]bool, len(obj))
		for k := range obj {
			n.keys[k] = true
		}
		node(path+".condition", n.Condition, obj["condition"])
		var list []json.RawMessage
		_ = json.Unmarshal(obj["conditions"], &list)
		for i, c := range list {
			if i < len(n.Conditions) {
				node(fmt.Sprintf("%s.conditions[%d]", path, i), n.Conditions[i], c)
			}
		}
	}
	node("condition", r.Condition, doc["condition"])
	node("resolve", r.Resolve, doc["resolve"])
	return problems
}

func cleanJSONError(err error) string {
	msg := err.Error()
	return strings.TrimPrefix(msg, "json: ")
}

// ConditionHash fingerprints everything that shapes evaluation state: condition,
// resolve condition, scope, sources and alerting mode. Presentation changes (name,
// severity, actions, cooldown) keep the hash, so state carries over; anything else
// resets the state deliberately. Device ids and sources are sets: their order is not part
// of the fingerprint (streams named only by absence nodes are part of the condition).
func ConditionHash(r *Rule) string {
	scope := r.Scope
	scope.DeviceIDs = sortedSet(scope.DeviceIDs)
	shape := struct {
		Condition *Node     `json:"c"`
		Resolve   *Node     `json:"r,omitempty"`
		Scope     Scope     `json:"s"`
		Streams   []string  `json:"st"`
		Alerting  *Alerting `json:"a,omitempty"`
	}{r.Condition, r.Resolve, scope, sortedSet(r.SourceStreams()), nil}
	// The effective settings, so spelling out a default changes nothing.
	if mode, n := r.Mode(); mode == ModePerEvent {
		shape.Alerting = &Alerting{Mode: mode, MaxActive: &n}
	}
	b, _ := json.Marshal(shape)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:12])
}

func sortedSet(list []string) []string {
	out := slices.Clone(list)
	slices.Sort(out)
	return slices.Compact(out)
}
