// Package eval compiles a validated rule condition into an evaluation tree and runs
// it against one event (or one timer tick) and the per-rule-per-device state.
//
// Evaluation is pure and deterministic: no clock, no I/O. The caller passes the
// time, the event and the state; the state is mutated in place and is plain JSON so
// it can be persisted and recovered after a restart.
//
// Every node returns two answers:
//   - v: the outcome at the event-time frontier of the device, which drives alerts;
//   - raw: what this event alone says, which window nodes count. They differ only
//     for an out-of-order event, which may still land in a window but must not flip
//     an order-sensitive condition backwards.
package eval

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
)

// Kind of a field value.
type Kind uint8

const (
	KindNumber Kind = iota + 1
	KindString
	KindBool
)

// Value is one scalar field from an event payload.
type Value struct {
	Kind Kind
	Num  float64
	Str  string
	Bool bool
}

func Num(f float64) Value { return Value{Kind: KindNumber, Num: f} }
func Str(s string) Value  { return Value{Kind: KindString, Str: s} }
func Bool(b bool) Value   { return Value{Kind: KindBool, Bool: b} }

// Event is the engine's view of a normalized event. Identity fields come from the
// authenticated envelope, never from Fields.
type Event struct {
	ID       string
	OrgID    string
	DeviceID string
	Kind     string
	Stream   string
	Time     time.Time
	Fields   map[string]Value
}

// FlattenJSON turns a JSON object into dotted scalar fields ({"net":{"rx":1}} ->
// "net.rx"). Arrays and nulls are skipped; nesting stops at 6 levels and 256 fields.
func FlattenJSON(data []byte) (map[string]Value, error) {
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil, fmt.Errorf("payload is not a JSON object: %w", err)
	}
	out := make(map[string]Value, len(obj))
	flatten("", obj, out, 0)
	return out, nil
}

func flatten(prefix string, obj map[string]any, out map[string]Value, depth int) {
	for k, v := range obj {
		if len(out) >= 256 {
			return
		}
		key := k
		if prefix != "" {
			key = prefix + "." + k
		}
		switch t := v.(type) {
		case float64:
			out[key] = Num(t)
		case string:
			out[key] = Str(t)
		case bool:
			out[key] = Bool(t)
		case map[string]any:
			if depth < 5 {
				flatten(key, t, out, depth+1)
			}
		}
	}
}

// Bucket summarizes the samples that fell into one slice of a window.
type Bucket struct {
	S   int64   `json:"s"` // slice start, unix ms
	N   int     `json:"n"` // samples
	Sum float64 `json:"sum,omitempty"`
	Min float64 `json:"min,omitempty"`
	Max float64 `json:"max,omitempty"`
	F   float64 `json:"f,omitempty"`  // earliest sample value
	FT  int64   `json:"ft,omitempty"` // earliest sample time, unix ms
	L   float64 `json:"l,omitempty"`  // latest sample value
	LT  int64   `json:"lt,omitempty"` // latest sample time, unix ms
}

// NodeState is the persisted state of one stateful node. Fields are short and
// omitted when empty, because one row exists per rule and device.
type NodeState struct {
	Last    *bool      `json:"o,omitempty"` // remembered outcome
	Since   *time.Time `json:"s,omitempty"` // duration start, or absence last-seen
	Count   int        `json:"n,omitempty"`
	Buckets []Bucket   `json:"b,omitempty"`
}

// State maps node paths ("c", "c.0.1", "r") to their state.
type State map[string]*NodeState

func (s State) get(path string) *NodeState {
	ns, ok := s[path]
	if !ok {
		ns = &NodeState{}
		s[path] = ns
	}
	return ns
}

// BucketsPerWindow is how many slices a window is split into, so a window's state is
// bounded whatever the event rate. A 10 minute window resolves to 10 seconds.
const BucketsPerWindow = 60

// Input is one evaluation.
type Input struct {
	Event         *Event    // nil for a timer tick
	Now           time.Time // event-time frontier for events, wall clock for ticks
	OutOfOrder    bool      // the event is older than the newest one seen for the device
	LateTolerance time.Duration
	ActivatedAt   time.Time // when this rule started watching the device (absence baseline)
}

// Output is the result of one evaluation.
type Output struct {
	Fire     bool
	Pending  bool
	Resolve  *bool     // outcome of the resolve condition, when the rule has one
	Deadline time.Time // earliest time a timer must re-evaluate, zero if none
	Values   map[string]float64
}

type result struct {
	v, raw   bool
	pending  bool
	deadline time.Time
}

type ctx struct {
	in     Input
	st     State
	values map[string]float64
}

type node interface {
	eval(c *ctx) result
}

// Program is a compiled rule condition.
type Program struct {
	root, resolve node
	streams       map[string]bool
	absence       bool
}

// Compile builds the evaluation tree of a validated rule.
func Compile(r *dsl.Rule) (*Program, error) {
	p := &Program{streams: map[string]bool{}}
	for _, s := range r.Streams() {
		p.streams[s] = true
	}
	var err error
	if p.root, err = compile(r.Condition, "c"); err != nil {
		return nil, err
	}
	if r.Resolve != nil {
		if p.resolve, err = compile(r.Resolve, "r"); err != nil {
			return nil, err
		}
	}
	p.absence = dsl.ContainsType(r.Condition, dsl.NodeAbsence)
	return p, nil
}

// Reads reports whether the rule consumes events from the stream.
func (p *Program) Reads(stream string) bool { return p.streams[stream] }

// HasAbsence reports whether the rule waits for missing events.
func (p *Program) HasAbsence() bool { return p.absence }

// Evaluate runs the condition (and the resolve condition) and mutates st.
func (p *Program) Evaluate(in Input, st State) Output {
	c := &ctx{in: in, st: st, values: map[string]float64{}}
	r := p.root.eval(c)
	out := Output{Fire: r.v, Pending: r.pending && !r.v, Deadline: r.deadline, Values: c.values}
	if p.resolve != nil {
		rr := p.resolve.eval(c)
		out.Resolve = &rr.v
		out.Deadline = minTime(out.Deadline, rr.deadline)
	}
	return out
}

func minTime(a, b time.Time) time.Time {
	if a.IsZero() {
		return b
	}
	if b.IsZero() || a.Before(b) {
		return a
	}
	return b
}

func compile(n *dsl.Node, path string) (node, error) {
	switch n.Type {
	case dsl.NodeComparison:
		return compileComparison(n, path)
	case dsl.NodeAll, dsl.NodeAny:
		kids := make([]node, len(n.Conditions))
		for i, c := range n.Conditions {
			k, err := compile(c, fmt.Sprintf("%s.%d", path, i))
			if err != nil {
				return nil, err
			}
			kids[i] = k
		}
		return &logical{all: n.Type == dsl.NodeAll, kids: kids}, nil
	case dsl.NodeNot:
		k, err := compile(n.Condition, path+".0")
		if err != nil {
			return nil, err
		}
		return &not{kid: k}, nil
	case dsl.NodeDuration:
		k, err := compile(n.Condition, path+".0")
		if err != nil {
			return nil, err
		}
		return &duration{path: path, d: n.Duration.D(), kid: k}, nil
	case dsl.NodeConsecutive:
		k, err := compile(n.Condition, path+".0")
		if err != nil {
			return nil, err
		}
		return &consecutive{path: path, n: n.Count, kid: k}, nil
	case dsl.NodeCountWithin:
		k, err := compile(n.Condition, path+".0")
		if err != nil {
			return nil, err
		}
		return &countWithin{path: path, w: n.Window.D(), min: n.MinCount, kid: k}, nil
	case dsl.NodeAggregate:
		cmp, err := compileNumericTest(n)
		if err != nil {
			return nil, err
		}
		return &aggregate{path: path, w: n.Window.D(), fn: n.Function, field: n.Field, test: cmp}, nil
	case dsl.NodeAbsence:
		set := map[string]bool{}
		for _, s := range n.Streams {
			set[s] = true
		}
		return &absence{path: path, d: n.Duration.D(), streams: set}, nil
	}
	return nil, fmt.Errorf("unknown node type %q at %s", n.Type, path)
}

// ---- comparison

type comparison struct {
	path   string
	field  string
	op     string
	value  any
	set    []any
	lo, hi float64
}

func compileComparison(n *dsl.Node, path string) (node, error) {
	c := &comparison{path: path, field: n.Field, op: n.Operator}
	switch n.Operator {
	case dsl.OpEq, dsl.OpNe, dsl.OpGt, dsl.OpGte, dsl.OpLt, dsl.OpLte:
		v, ok := dsl.Scalar(n.Value)
		if !ok {
			return nil, fmt.Errorf("%s: invalid value", path)
		}
		c.value = v
	case dsl.OpBetween:
		c.lo, c.hi = *n.Min, *n.Max
	case dsl.OpIn, dsl.OpNotIn:
		for _, raw := range n.Values {
			v, ok := dsl.Scalar(raw)
			if !ok {
				return nil, fmt.Errorf("%s: invalid value in list", path)
			}
			c.set = append(c.set, v)
		}
	}
	return c, nil
}

func equal(v Value, want any) bool {
	switch w := want.(type) {
	case float64:
		return v.Kind == KindNumber && v.Num == w
	case string:
		return v.Kind == KindString && v.Str == w
	case bool:
		return v.Kind == KindBool && v.Bool == w
	}
	return false
}

func (c *comparison) test(v Value, ok bool) bool {
	switch c.op {
	case dsl.OpExists:
		return ok
	case dsl.OpNotExists:
		return !ok
	case dsl.OpEq:
		return equal(v, c.value)
	case dsl.OpNe:
		return !equal(v, c.value)
	case dsl.OpIn, dsl.OpNotIn:
		hit := false
		for _, w := range c.set {
			if equal(v, w) {
				hit = true
				break
			}
		}
		return hit == (c.op == dsl.OpIn)
	}
	if v.Kind != KindNumber {
		return false
	}
	switch c.op {
	case dsl.OpGt:
		return v.Num > c.value.(float64)
	case dsl.OpGte:
		return v.Num >= c.value.(float64)
	case dsl.OpLt:
		return v.Num < c.value.(float64)
	case dsl.OpLte:
		return v.Num <= c.value.(float64)
	case dsl.OpBetween:
		return v.Num >= c.lo && v.Num <= c.hi
	}
	return false
}

// A comparison remembers its last outcome. An event that does not carry the field
// keeps that outcome (devices may report metrics in separate messages); a timer tick
// re-reads it; an out-of-order event is evaluated for windows (raw) but cannot change it.
func (c *comparison) eval(cx *ctx) result {
	ns := cx.st.get(c.path)
	last := ns.Last != nil && *ns.Last
	ev := cx.in.Event
	if ev == nil {
		return result{v: last, raw: last}
	}
	v, ok := ev.Fields[c.field]
	if !ok && c.op != dsl.OpExists && c.op != dsl.OpNotExists {
		return result{v: last, raw: false}
	}
	got := c.test(v, ok)
	if ok && v.Kind == KindNumber {
		cx.values[c.field] = v.Num
	}
	if cx.in.OutOfOrder {
		return result{v: last, raw: got}
	}
	if ns.Last == nil || *ns.Last != got {
		b := got
		ns.Last = &b
	}
	return result{v: got, raw: got}
}

// ---- all / any / not

type logical struct {
	all  bool
	kids []node
}

func (l *logical) eval(cx *ctx) result {
	out := result{v: l.all, raw: l.all}
	anyPending, allSatisfiable := false, true
	for _, k := range l.kids { // every child runs, so every child's state stays current
		r := k.eval(cx)
		if l.all {
			out.v = out.v && r.v
			out.raw = out.raw && r.raw
		} else {
			out.v = out.v || r.v
			out.raw = out.raw || r.raw
		}
		anyPending = anyPending || r.pending
		allSatisfiable = allSatisfiable && (r.v || r.pending)
		out.deadline = minTime(out.deadline, r.deadline)
	}
	if !out.v {
		if l.all {
			out.pending = anyPending && allSatisfiable
		} else {
			out.pending = anyPending
		}
	}
	return out
}

type not struct{ kid node }

func (n *not) eval(cx *ctx) result {
	r := n.kid.eval(cx)
	return result{v: !r.v, raw: !r.raw, deadline: r.deadline}
}

// ---- duration: the child holds continuously for d

type duration struct {
	path string
	d    time.Duration
	kid  node
}

func (d *duration) eval(cx *ctx) result {
	r := d.kid.eval(cx)
	ns := cx.st.get(d.path)
	ev := cx.in.Event
	if ev == nil || !cx.in.OutOfOrder {
		switch {
		case r.v && ns.Since == nil:
			t := cx.in.Now
			if ev != nil {
				t = ev.Time
			}
			ns.Since = &t
		case !r.v && ns.Since != nil:
			ns.Since = nil
		}
	}
	if ns.Since == nil {
		return result{deadline: r.deadline}
	}
	due := ns.Since.Add(d.d)
	if !cx.in.Now.Before(due) {
		return result{v: true, raw: true, deadline: r.deadline}
	}
	return result{pending: true, deadline: minTime(due, r.deadline)}
}

// ---- consecutive: the child holds on n events in a row

type consecutive struct {
	path string
	n    int
	kid  node
}

func (c *consecutive) eval(cx *ctx) result {
	r := c.kid.eval(cx)
	ns := cx.st.get(c.path)
	if cx.in.Event != nil && !cx.in.OutOfOrder {
		if r.raw {
			if ns.Count < c.n {
				ns.Count++
			}
		} else {
			ns.Count = 0
		}
	}
	v := ns.Count >= c.n
	return result{v: v, raw: v, pending: !v && ns.Count > 0, deadline: r.deadline}
}

// ---- windows

func width(w time.Duration) int64 {
	ms := w.Milliseconds() / BucketsPerWindow
	if ms < 1 {
		ms = 1
	}
	return ms
}

// addSample puts one sample into its slice, keeping buckets sorted by start.
func addSample(ns *NodeState, w time.Duration, t time.Time, val float64, hasVal bool) {
	wd := width(w)
	ms := t.UnixMilli()
	start := ms - mod(ms, wd)
	i := 0
	for i < len(ns.Buckets) && ns.Buckets[i].S < start {
		i++
	}
	if i == len(ns.Buckets) || ns.Buckets[i].S != start {
		ns.Buckets = append(ns.Buckets, Bucket{})
		copy(ns.Buckets[i+1:], ns.Buckets[i:])
		ns.Buckets[i] = Bucket{S: start}
	}
	b := &ns.Buckets[i]
	if !hasVal {
		b.N++
		return
	}
	if b.N == 0 {
		b.Min, b.Max, b.F, b.FT, b.L, b.LT = val, val, val, ms, val, ms
	} else {
		b.Min = math.Min(b.Min, val)
		b.Max = math.Max(b.Max, val)
		if ms < b.FT {
			b.F, b.FT = val, ms
		}
		if ms >= b.LT {
			b.L, b.LT = val, ms
		}
	}
	b.N++
	b.Sum += val
}

func mod(a, b int64) int64 {
	m := a % b
	if m < 0 {
		m += b
	}
	return m
}

// prune drops slices that can no longer enter the window, even for a late event.
func prune(ns *NodeState, w time.Duration, now time.Time, late time.Duration) {
	cut := now.Add(-w - late).UnixMilli()
	wd := width(w)
	i := 0
	for i < len(ns.Buckets) && ns.Buckets[i].S+wd <= cut {
		i++
	}
	if i > 0 {
		ns.Buckets = append(ns.Buckets[:0], ns.Buckets[i:]...)
	}
	if len(ns.Buckets) == 0 {
		ns.Buckets = nil
	}
}

// inWindow returns the slices that overlap (now-w, now] and the time the earliest of
// them leaves the window.
func inWindow(ns *NodeState, w time.Duration, now time.Time) ([]Bucket, time.Time) {
	wd := width(w)
	from := now.Add(-w).UnixMilli()
	to := now.UnixMilli()
	var out []Bucket
	var leaves time.Time
	for _, b := range ns.Buckets {
		if b.S+wd > from && b.S <= to {
			if len(out) == 0 {
				leaves = time.UnixMilli(b.S + wd).Add(w)
			}
			out = append(out, b)
		}
	}
	return out, leaves
}

type countWithin struct {
	path string
	w    time.Duration
	min  int
	kid  node
}

func (c *countWithin) eval(cx *ctx) result {
	r := c.kid.eval(cx)
	ns := cx.st.get(c.path)
	if ev := cx.in.Event; ev != nil && r.raw {
		addSample(ns, c.w, ev.Time, 0, false)
	}
	prune(ns, c.w, cx.in.Now, cx.in.LateTolerance)
	bs, leaves := inWindow(ns, c.w, cx.in.Now)
	n := 0
	for _, b := range bs {
		n += b.N
	}
	v := n >= c.min
	return result{v: v, raw: v, pending: !v && n > 0, deadline: minTime(leaves, r.deadline)}
}

type numericTest struct {
	op     string
	value  float64
	lo, hi float64
}

func compileNumericTest(n *dsl.Node) (numericTest, error) {
	t := numericTest{op: n.Operator}
	if n.Operator == dsl.OpBetween {
		t.lo, t.hi = *n.Min, *n.Max
		return t, nil
	}
	v, ok := dsl.Number(n.Value)
	if !ok {
		return t, fmt.Errorf("aggregate value must be a number")
	}
	t.value = v
	return t, nil
}

func (t numericTest) test(x float64) bool {
	switch t.op {
	case dsl.OpEq:
		return x == t.value
	case dsl.OpNe:
		return x != t.value
	case dsl.OpGt:
		return x > t.value
	case dsl.OpGte:
		return x >= t.value
	case dsl.OpLt:
		return x < t.value
	case dsl.OpLte:
		return x <= t.value
	case dsl.OpBetween:
		return x >= t.lo && x <= t.hi
	}
	return false
}

type aggregate struct {
	path  string
	w     time.Duration
	fn    string
	field string
	test  numericTest
}

func (a *aggregate) eval(cx *ctx) result {
	ns := cx.st.get(a.path)
	if ev := cx.in.Event; ev != nil {
		if v, ok := ev.Fields[a.field]; ok && v.Kind == KindNumber && !math.IsNaN(v.Num) && !math.IsInf(v.Num, 0) {
			addSample(ns, a.w, ev.Time, v.Num, true)
		}
	}
	prune(ns, a.w, cx.in.Now, cx.in.LateTolerance)
	bs, leaves := inWindow(ns, a.w, cx.in.Now)
	x, ok := a.compute(bs)
	if !ok {
		return result{deadline: leaves}
	}
	cx.values[a.fn+"("+a.field+")"] = x
	v := a.test.test(x)
	return result{v: v, raw: v, deadline: leaves}
}

func (a *aggregate) compute(bs []Bucket) (float64, bool) {
	n := 0
	var sum float64
	lo, hi := math.Inf(1), math.Inf(-1)
	var first, last float64
	var ft, lt int64
	for _, b := range bs {
		if b.N == 0 {
			continue
		}
		if n == 0 || b.FT < ft {
			first, ft = b.F, b.FT
		}
		if n == 0 || b.LT >= lt {
			last, lt = b.L, b.LT
		}
		n += b.N
		sum += b.Sum
		lo = math.Min(lo, b.Min)
		hi = math.Max(hi, b.Max)
	}
	if a.fn == dsl.FnCount {
		return float64(n), true
	}
	if n == 0 {
		return 0, false
	}
	switch a.fn {
	case dsl.FnAvg:
		return sum / float64(n), true
	case dsl.FnSum:
		return sum, true
	case dsl.FnMin:
		return lo, true
	case dsl.FnMax:
		return hi, true
	case dsl.FnDelta:
		if n < 2 {
			return 0, false
		}
		return last - first, true
	case dsl.FnRate:
		if lt <= ft {
			return 0, false
		}
		return (last - first) / (float64(lt-ft) / 1000), true
	}
	return 0, false
}

// ---- absence: no matching event for d

type absence struct {
	path    string
	d       time.Duration
	streams map[string]bool
}

func (a *absence) eval(cx *ctx) result {
	ns := cx.st.get(a.path)
	if ev := cx.in.Event; ev != nil && (len(a.streams) == 0 || a.streams[ev.Stream]) {
		if ns.Since == nil || ev.Time.After(*ns.Since) {
			t := ev.Time
			ns.Since = &t
		}
	}
	seen := cx.in.ActivatedAt
	if ns.Since != nil {
		seen = *ns.Since
	}
	due := seen.Add(a.d)
	if !cx.in.Now.Before(due) {
		return result{v: true, raw: true}
	}
	return result{deadline: due}
}

// Describe renders a short human summary of a node, for logs and test output.
func Describe(n *dsl.Node) string {
	if n == nil {
		return ""
	}
	switch n.Type {
	case dsl.NodeComparison:
		return fmt.Sprintf("%s %s %s", n.Field, n.Operator, strings.TrimSpace(string(n.Value)))
	case dsl.NodeAll, dsl.NodeAny:
		parts := make([]string, len(n.Conditions))
		for i, c := range n.Conditions {
			parts[i] = Describe(c)
		}
		sep := " AND "
		if n.Type == dsl.NodeAny {
			sep = " OR "
		}
		return "(" + strings.Join(parts, sep) + ")"
	case dsl.NodeNot:
		return "NOT " + Describe(n.Condition)
	}
	return n.Type
}
