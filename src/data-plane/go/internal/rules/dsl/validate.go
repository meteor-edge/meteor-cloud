package dsl

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"meteorcloud/dataplane/internal/streams"
)

// Problem is one validation finding, addressed by a JSON path such as
// "condition.conditions[1].value".
type Problem struct {
	Path    string `json:"path"`
	Message string `json:"message"`
}

// ValidationError lists every problem found, not only the first.
type ValidationError struct {
	Problems []Problem `json:"problems"`
}

func (e *ValidationError) Error() string {
	parts := make([]string, 0, len(e.Problems))
	for _, p := range e.Problems {
		if p.Path == "" {
			parts = append(parts, p.Message)
		} else {
			parts = append(parts, p.Path+": "+p.Message)
		}
	}
	return "invalid rule: " + strings.Join(parts, "; ")
}

type validator struct {
	lim      Limits
	problems []Problem
	nodes    int
}

func (v *validator) add(path, format string, args ...any) {
	v.problems = append(v.problems, Problem{Path: path, Message: fmt.Sprintf(format, args...)})
}

// Validate checks a parsed rule against the schema and the limits. allowPlatform is
// true only for callers acting as a platform administrator.
func Validate(r *Rule, lim Limits, allowPlatform bool) error {
	v := &validator{lim: lim}
	if r.SchemaVersion != SchemaVersion {
		v.add("schema_version", "must be %d", SchemaVersion)
	}
	if n := strings.TrimSpace(r.Name); n == "" || len(n) > 200 {
		v.add("name", "is required and at most 200 characters")
	}
	if len(r.Description) > 2000 {
		v.add("description", "is at most 2000 characters")
	}
	switch r.Severity {
	case "", SeverityInfo, SeverityWarning, SeverityCritical:
	default:
		v.add("severity", "must be info, warning or critical")
	}
	v.scope(r.Scope, allowPlatform)
	if r.Source != nil {
		v.streamList("source.streams", r.Source.Streams)
	}
	if r.Condition == nil {
		v.add("condition", "is required")
	} else {
		v.node("condition", r.Condition, 1)
	}
	if r.Resolve != nil {
		v.node("resolve", r.Resolve, 1)
		if containsType(r.Resolve, NodeAbsence) {
			v.add("resolve", "absence cannot be used in a resolve condition")
		}
	}
	if r.Cooldown < 0 || r.Cooldown.D() > lim.MaxWindow {
		v.add("cooldown", "must be between 0s and %s", lim.MaxWindow)
	}
	if r.LateTolerance != nil && (r.LateTolerance.D() < 0 || r.LateTolerance.D() > lim.MaxWindow) {
		v.add("late_tolerance", "must be between 0s and %s", lim.MaxWindow)
	}
	if r.Alerting != nil {
		switch r.Alerting.Mode {
		case "", ModeSingle:
			if r.Alerting.MaxActive > 1 {
				v.add("alerting.max_active", "applies only to per_event mode")
			}
		case ModePerEvent:
			if r.Alerting.MaxActive < 0 || r.Alerting.MaxActive > 100 {
				v.add("alerting.max_active", "must be between 1 and 100")
			}
		default:
			v.add("alerting.mode", "must be single or per_event")
		}
	}
	v.actions(r.Actions)
	if len(v.problems) > 0 {
		return &ValidationError{Problems: v.problems}
	}
	return nil
}

// ParseAndValidate is Parse followed by Validate.
func ParseAndValidate(raw []byte, lim Limits, allowPlatform bool) (*Rule, error) {
	r, err := Parse(raw)
	if err != nil {
		return nil, err
	}
	if err := Validate(r, lim, allowPlatform); err != nil {
		return nil, err
	}
	return r, nil
}

func (v *validator) scope(s Scope, allowPlatform bool) {
	switch s.Type {
	case ScopeDevice:
		if len(s.DeviceIDs) == 0 || len(s.DeviceIDs) > v.lim.MaxDevices {
			v.add("scope.device_ids", "needs 1 to %d device ids", v.lim.MaxDevices)
		}
		for i, id := range s.DeviceIDs {
			if !validRef(id) {
				v.add(fmt.Sprintf("scope.device_ids[%d]", i), "is not a valid id")
			}
		}
	case ScopeGroup:
		if !validRef(s.GroupID) {
			v.add("scope.group_id", "is required for a group scope")
		}
	case ScopeDeviceType:
		if !validRef(s.DeviceTypeID) {
			v.add("scope.device_type_id", "is required for a device_type scope")
		}
	case ScopeOrganization:
	case ScopePlatform:
		if !allowPlatform {
			v.add("scope.type", "platform rules are for platform administrators only")
		}
	default:
		v.add("scope.type", "must be device, group, device_type, organization or platform")
	}
	if s.Type != ScopeDevice && len(s.DeviceIDs) > 0 {
		v.add("scope.device_ids", "applies only to a device scope")
	}
	if s.Type != ScopeGroup && s.GroupID != "" {
		v.add("scope.group_id", "applies only to a group scope")
	}
	if s.Type != ScopeDeviceType && s.DeviceTypeID != "" {
		v.add("scope.device_type_id", "applies only to a device_type scope")
	}
	if len(s.Labels) > 20 {
		v.add("scope.labels", "at most 20 labels")
	}
	for k, val := range s.Labels {
		if k == "" || len(k) > 64 || len(val) > 256 {
			v.add("scope.labels", "label keys are 1-64 characters, values at most 256")
			break
		}
	}
}

func validRef(id string) bool {
	if id == "" || len(id) > 64 {
		return false
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

func (v *validator) streamList(path string, list []string) {
	if len(list) > 10 {
		v.add(path, "at most 10 streams")
	}
	for i, s := range list {
		if err := streams.ValidateName(s); err != nil {
			v.add(fmt.Sprintf("%s[%d]", path, i), "%v", err)
		}
	}
}

func validField(f string) bool {
	if f == "" || len(f) > 128 {
		return false
	}
	for _, seg := range strings.Split(f, ".") {
		if seg == "" {
			return false
		}
		for i := 0; i < len(seg); i++ {
			c := seg[i]
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
				return false
			}
		}
	}
	return true
}

func (v *validator) window(path string, d Duration) {
	if d.D() < v.lim.MinWindow || d.D() > v.lim.MaxWindow {
		v.add(path, "must be between %s and %s", v.lim.MinWindow, v.lim.MaxWindow)
	}
}

func (v *validator) node(path string, n *Node, depth int) {
	if n == nil {
		v.add(path, "is required")
		return
	}
	v.nodes++
	if v.nodes == v.lim.MaxNodes+1 {
		v.add(path, "the rule has more than %d condition nodes", v.lim.MaxNodes)
	}
	if depth > v.lim.MaxDepth {
		v.add(path, "conditions are nested deeper than %d levels", v.lim.MaxDepth)
		return
	}
	unexpected := func(names ...string) {
		for _, name := range names {
			if nodeHas(n, name) {
				v.add(path+"."+name, "does not apply to a %s node", n.Type)
			}
		}
	}
	child := func() {
		if len(n.Conditions) > 0 {
			v.add(path+".conditions", "use condition (one child) for a %s node", n.Type)
		}
		v.node(path+".condition", n.Condition, depth+1)
	}
	switch n.Type {
	case NodeComparison:
		v.comparison(path, n)
		unexpected("conditions", "condition", "duration", "window", "min_count", "count", "function", "streams")
	case NodeAll, NodeAny:
		if len(n.Conditions) < 1 || len(n.Conditions) > 20 {
			v.add(path+".conditions", "needs 1 to 20 conditions")
		}
		for i, c := range n.Conditions {
			v.node(fmt.Sprintf("%s.conditions[%d]", path, i), c, depth+1)
		}
		unexpected("field", "operator", "value", "values", "min", "max", "condition", "duration", "window", "min_count", "count", "function", "streams")
	case NodeNot:
		child()
		unexpected("field", "operator", "value", "values", "min", "max", "duration", "window", "min_count", "count", "function", "streams")
	case NodeDuration:
		v.window(path+".duration", n.Duration)
		child()
		unexpected("field", "operator", "value", "values", "min", "max", "window", "min_count", "count", "function", "streams")
	case NodeCountWithin:
		v.window(path+".window", n.Window)
		if n.MinCount < 1 || n.MinCount > 100000 {
			v.add(path+".min_count", "must be between 1 and 100000")
		}
		child()
		unexpected("field", "operator", "value", "values", "min", "max", "duration", "count", "function", "streams")
	case NodeConsecutive:
		if n.Count < 1 || n.Count > 10000 {
			v.add(path+".count", "must be between 1 and 10000")
		}
		child()
		unexpected("field", "operator", "value", "values", "min", "max", "duration", "window", "min_count", "function", "streams")
	case NodeAggregate:
		v.window(path+".window", n.Window)
		switch n.Function {
		case FnAvg, FnMin, FnMax, FnSum, FnCount, FnDelta, FnRate:
		default:
			v.add(path+".function", "must be avg, min, max, sum, count, delta or rate")
		}
		if !validField(n.Field) {
			v.add(path+".field", "must be a dotted field name")
		}
		switch n.Operator {
		case OpEq, OpNe, OpGt, OpGte, OpLt, OpLte:
			if _, ok := number(n.Value); !ok {
				v.add(path+".value", "must be a number")
			}
		case OpBetween:
			v.between(path, n)
		default:
			v.add(path+".operator", "must be eq, ne, gt, gte, lt, lte or between for an aggregate")
		}
		unexpected("conditions", "condition", "duration", "min_count", "count", "streams", "values")
	case NodeAbsence:
		v.window(path+".duration", n.Duration)
		v.streamList(path+".streams", n.Streams)
		unexpected("field", "operator", "value", "values", "min", "max", "conditions", "condition", "window", "min_count", "count", "function")
	case "":
		v.add(path+".type", "is required")
	default:
		v.add(path+".type", "unknown node type %q", n.Type)
	}
}

func (v *validator) comparison(path string, n *Node) {
	if !validField(n.Field) {
		v.add(path+".field", "must be a dotted field name")
	}
	switch n.Operator {
	case OpEq, OpNe:
		if _, ok := scalar(n.Value); !ok {
			v.add(path+".value", "must be a number, string or boolean")
		}
	case OpGt, OpGte, OpLt, OpLte:
		if _, ok := number(n.Value); !ok {
			v.add(path+".value", "must be a number")
		}
	case OpBetween:
		v.between(path, n)
	case OpIn, OpNotIn:
		if len(n.Values) == 0 || len(n.Values) > v.lim.MaxListItems {
			v.add(path+".values", "needs 1 to %d values", v.lim.MaxListItems)
		}
		for i, raw := range n.Values {
			if _, ok := scalar(raw); !ok {
				v.add(fmt.Sprintf("%s.values[%d]", path, i), "must be a number, string or boolean")
			}
		}
	case OpExists, OpNotExists:
		if len(n.Value) > 0 {
			v.add(path+".value", "does not apply to %s", n.Operator)
		}
	case "":
		v.add(path+".operator", "is required")
	default:
		v.add(path+".operator", "unknown operator %q", n.Operator)
	}
	if n.Operator != OpIn && n.Operator != OpNotIn && len(n.Values) > 0 {
		v.add(path+".values", "applies only to in and not_in")
	}
	if n.Operator != OpBetween && (n.Min != nil || n.Max != nil) {
		v.add(path+".min", "min and max apply only to between")
	}
}

func (v *validator) between(path string, n *Node) {
	if n.Min == nil || n.Max == nil {
		v.add(path, "between needs min and max")
	} else if *n.Min > *n.Max {
		v.add(path+".min", "must not be greater than max")
	}
	if len(n.Value) > 0 {
		v.add(path+".value", "between uses min and max, not value")
	}
}

func (v *validator) actions(acts []Action) {
	if len(acts) > v.lim.MaxActions {
		v.add("actions", "at most %d actions", v.lim.MaxActions)
	}
	alerts := 0
	for i, a := range acts {
		p := fmt.Sprintf("actions[%d]", i)
		switch a.Type {
		case ActionAlert:
			alerts++
			switch a.Severity {
			case "", SeverityInfo, SeverityWarning, SeverityCritical:
			default:
				v.add(p+".severity", "must be info, warning or critical")
			}
		case ActionWebhook, ActionNotification:
			if !validRef(a.IntegrationID) {
				v.add(p+".integration_id", "is required")
			}
		case ActionCommand:
			if a.Command == "" || len(a.Command) > 64 || !validField(a.Command) {
				v.add(p+".command", "is required (1-64 characters of a-z 0-9 . _ -)")
			}
		case ActionPlatformEvent:
		default:
			v.add(p+".type", "must be alert, webhook, command, notification or platform_event")
		}
		for _, on := range a.On {
			if on != OnFiring && on != OnResolved {
				v.add(p+".on", "entries must be firing or resolved")
			}
		}
		if len(a.Params) > 0 {
			t := bytes.TrimSpace(a.Params)
			if len(t) > 4096 || t[0] != '{' || !json.Valid(t) {
				v.add(p+".params", "must be a JSON object of at most 4 KiB")
			}
		}
		if a.Timeout < 0 || a.Timeout.D() > 60e9 {
			v.add(p+".timeout", "must be between 0s and 60s")
		}
	}
	if alerts > 1 {
		v.add("actions", "at most one alert action")
	}
}

// number decodes a JSON number.
func number(raw json.RawMessage) (float64, bool) {
	if len(raw) == 0 {
		return 0, false
	}
	var f float64
	if err := json.Unmarshal(raw, &f); err != nil {
		return 0, false
	}
	return f, true
}

// scalar decodes a JSON number, string or boolean.
func scalar(raw json.RawMessage) (any, bool) {
	if len(raw) == 0 {
		return nil, false
	}
	var x any
	if err := json.Unmarshal(raw, &x); err != nil {
		return nil, false
	}
	switch t := x.(type) {
	case float64, bool:
		return t, true
	case string:
		return t, len(t) <= 256
	}
	return nil, false
}

// Scalar exposes scalar decoding to the compiler.
func Scalar(raw json.RawMessage) (any, bool) { return scalar(raw) }

// Number exposes number decoding to the compiler.
func Number(raw json.RawMessage) (float64, bool) { return number(raw) }

func nodeHas(n *Node, name string) bool {
	switch name {
	case "field":
		return n.Field != ""
	case "operator":
		return n.Operator != ""
	case "value":
		return len(n.Value) > 0
	case "values":
		return len(n.Values) > 0
	case "min":
		return n.Min != nil
	case "max":
		return n.Max != nil
	case "conditions":
		return len(n.Conditions) > 0
	case "condition":
		return n.Condition != nil
	case "duration":
		return n.Duration != 0
	case "window":
		return n.Window != 0
	case "min_count":
		return n.MinCount != 0
	case "count":
		return n.Count != 0
	case "function":
		return n.Function != ""
	case "streams":
		return len(n.Streams) > 0
	}
	return false
}

func containsType(n *Node, t string) bool {
	if n == nil {
		return false
	}
	if n.Type == t {
		return true
	}
	for _, c := range n.Conditions {
		if containsType(c, t) {
			return true
		}
	}
	return containsType(n.Condition, t)
}

// ContainsType reports whether any node in the tree has the given type.
func ContainsType(n *Node, t string) bool { return containsType(n, t) }
