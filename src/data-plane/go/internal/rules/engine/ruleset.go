package engine

import (
	"encoding/json"
	"fmt"
	"sort"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/eval"
)

// Snapshot is what the control plane publishes for the engine
// (GET /internal/rules/snapshot): every enabled rule at its current version and the
// device index used for scoping. The engine never reads control-plane tables.
type Snapshot struct {
	Version string       `json:"version"`
	Rules   []RuleRecord `json:"rules"`
	Devices []Device     `json:"devices"`
}

// RuleRecord is one enabled rule version. OrganizationID is empty only for
// platform-wide rules created by a platform administrator.
type RuleRecord struct {
	ID             string          `json:"id"`
	OrganizationID string          `json:"organization_id"`
	Version        int             `json:"version"`
	Definition     json.RawMessage `json:"definition"`
}

// Device is the metadata rules are scoped by.
type Device struct {
	ID             string            `json:"id"`
	OrganizationID string            `json:"organization_id"`
	GroupID        string            `json:"group_id,omitempty"`
	DeviceTypeID   string            `json:"device_type_id,omitempty"`
	Labels         map[string]string `json:"labels,omitempty"`
}

// Rule is a compiled, enabled rule version.
type Rule struct {
	ID      string
	OrgID   string // empty for platform rules
	Version int
	Hash    string
	Def     *dsl.Rule
	Prog    *eval.Program
	Actions []dsl.Action
	devices map[string]bool
}

// Matches reports whether the rule applies to a device.
func (r *Rule) Matches(d *Device) bool {
	if r.OrgID != "" && r.OrgID != d.OrganizationID {
		return false
	}
	s := r.Def.Scope
	switch s.Type {
	case dsl.ScopeDevice:
		if !r.devices[d.ID] {
			return false
		}
	case dsl.ScopeGroup:
		if d.GroupID == "" || d.GroupID != s.GroupID {
			return false
		}
	case dsl.ScopeDeviceType:
		if d.DeviceTypeID == "" || d.DeviceTypeID != s.DeviceTypeID {
			return false
		}
	case dsl.ScopeOrganization, dsl.ScopePlatform:
	default:
		return false
	}
	for k, v := range s.Labels {
		if d.Labels[k] != v {
			return false
		}
	}
	return true
}

// RuleError is a snapshot rule the engine refused to load.
type RuleError struct {
	RuleID string `json:"rule_id"`
	Error  string `json:"error"`
}

// RuleSet is an immutable compiled snapshot. Readers load it atomically; a new
// snapshot replaces it whole.
type RuleSet struct {
	Version  string
	byID     map[string]*Rule
	byOrg    map[string][]*Rule
	platform []*Rule
	devices  map[string]*Device
	Errors   []RuleError
}

// CompileSnapshot builds a RuleSet. A rule that fails validation is skipped and
// reported, never half-loaded; the rest still load.
func CompileSnapshot(s *Snapshot, lim dsl.Limits) *RuleSet {
	rs := &RuleSet{Version: s.Version, byID: map[string]*Rule{}, byOrg: map[string][]*Rule{},
		devices: make(map[string]*Device, len(s.Devices))}
	for i := range s.Devices {
		d := s.Devices[i]
		rs.devices[d.ID] = &d
	}
	for _, rec := range s.Rules {
		r, err := CompileRule(rec, lim)
		if err != nil {
			rs.Errors = append(rs.Errors, RuleError{RuleID: rec.ID, Error: err.Error()})
			continue
		}
		if !r.Def.IsEnabled() {
			continue
		}
		rs.byID[r.ID] = r
		if r.OrgID == "" {
			rs.platform = append(rs.platform, r)
		} else {
			rs.byOrg[r.OrgID] = append(rs.byOrg[r.OrgID], r)
		}
	}
	for _, list := range rs.byOrg {
		sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })
	}
	sort.Slice(rs.platform, func(i, j int) bool { return rs.platform[i].ID < rs.platform[j].ID })
	return rs
}

// CompileRule validates and compiles one rule record.
func CompileRule(rec RuleRecord, lim dsl.Limits) (*Rule, error) {
	if rec.ID == "" {
		return nil, fmt.Errorf("rule id is required")
	}
	def, err := dsl.ParseAndValidate(rec.Definition, lim, rec.OrganizationID == "")
	if err != nil {
		return nil, err
	}
	if rec.OrganizationID == "" && def.Scope.Type != dsl.ScopePlatform {
		return nil, fmt.Errorf("a rule without an organization must have a platform scope")
	}
	if rec.OrganizationID != "" && def.Scope.Type == dsl.ScopePlatform {
		return nil, fmt.Errorf("an organization rule cannot have a platform scope")
	}
	prog, err := eval.Compile(def)
	if err != nil {
		return nil, err
	}
	r := &Rule{ID: rec.ID, OrgID: rec.OrganizationID, Version: rec.Version, Hash: dsl.ConditionHash(def),
		Def: def, Prog: prog, Actions: def.EffectiveActions(), devices: map[string]bool{}}
	for _, id := range def.Scope.DeviceIDs {
		r.devices[id] = true
	}
	return r, nil
}

// Device returns the device metadata, if the snapshot knows the device.
func (rs *RuleSet) Device(id string) (*Device, bool) {
	d, ok := rs.devices[id]
	return d, ok
}

// Rule returns an enabled rule by id.
func (rs *RuleSet) Rule(id string) (*Rule, bool) {
	r, ok := rs.byID[id]
	return r, ok
}

// Len is the number of enabled rules.
func (rs *RuleSet) Len() int { return len(rs.byID) }

// Candidates returns the rules that apply to a device and read the stream.
func (rs *RuleSet) Candidates(d *Device, stream string) []*Rule {
	var out []*Rule
	for _, list := range [][]*Rule{rs.byOrg[d.OrganizationID], rs.platform} {
		for _, r := range list {
			if r.Prog.Reads(stream) && r.Matches(d) {
				out = append(out, r)
			}
		}
	}
	return out
}

// MatchingDevices lists the devices a rule applies to (absence seeding, preview).
func (rs *RuleSet) MatchingDevices(r *Rule) []*Device {
	var out []*Device
	for _, d := range rs.devices {
		if r.Matches(d) {
			out = append(out, d)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Rules lists every enabled rule, sorted by id.
func (rs *RuleSet) Rules() []*Rule {
	out := make([]*Rule, 0, len(rs.byID))
	for _, r := range rs.byID {
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}
