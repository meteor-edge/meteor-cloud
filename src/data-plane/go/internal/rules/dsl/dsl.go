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

func (d Duration) D() time.Duration { return time.Duration(d) }

func (d Duration) MarshalJSON() ([]byte, error) { return json.Marshal(time.Duration(d).String()) }

func (d *Duration) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return fmt.Errorf("duration must be a string like \"5m\"")
	}
	v, err := time.ParseDuration(s)
	if err != nil {
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
}

// Source names the event streams that feed a rule.
type Source struct {
	Streams []string `json:"streams,omitempty"`
}

// Alerting controls how many alerts a rule may open per device.
type Alerting struct {
	Mode      string `json:"mode,omitempty"`
	MaxActive int    `json:"max_active,omitempty"`
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

// Streams returns the streams the rule reads.
func (r *Rule) Streams() []string {
	if r.Source == nil || len(r.Source.Streams) == 0 {
		return []string{DefaultStream}
	}
	return r.Source.Streams
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
	n := r.Alerting.MaxActive
	if n <= 0 {
		n = 10
	}
	return ModePerEvent, n
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
	if dec.More() {
		return nil, &ValidationError{Problems: []Problem{{Path: "", Message: "trailing data after the rule"}}}
	}
	return &r, nil
}

func cleanJSONError(err error) string {
	msg := err.Error()
	return strings.TrimPrefix(msg, "json: ")
}

// ConditionHash fingerprints everything that shapes evaluation state: condition,
// resolve condition, scope, sources and alerting mode. Presentation changes (name,
// severity, actions, cooldown) keep the hash, so state carries over; anything else
// resets the state deliberately.
func ConditionHash(r *Rule) string {
	shape := struct {
		Condition *Node     `json:"c"`
		Resolve   *Node     `json:"r,omitempty"`
		Scope     Scope     `json:"s"`
		Streams   []string  `json:"st"`
		Alerting  *Alerting `json:"a,omitempty"`
	}{r.Condition, r.Resolve, r.Scope, r.Streams(), r.Alerting}
	b, _ := json.Marshal(shape)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:12])
}
