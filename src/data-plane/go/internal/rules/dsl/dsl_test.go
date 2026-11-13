package dsl

import (
	"errors"
	"strings"
	"testing"
)

func mustValidate(t *testing.T, raw string, platform bool) error {
	t.Helper()
	_, err := ParseAndValidate([]byte(raw), DefaultLimits(), platform)
	return err
}

func TestValidRulesFromTheDocs(t *testing.T) {
	rules := []string{
		`{"schema_version":1,"name":"High CPU","severity":"critical","scope":{"type":"device","device_ids":["dev-1"]},
		  "condition":{"type":"comparison","field":"cpu_percent","operator":"gt","value":90},
		  "actions":[{"type":"alert","severity":"critical"}]}`,
		`{"schema_version":1,"name":"Sustained memory","scope":{"type":"organization","labels":{"env":"production"}},
		  "condition":{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"memory_percent","operator":"gt","value":90}}}`,
		`{"schema_version":1,"name":"Silent","scope":{"type":"group","group_id":"g1"},"condition":{"type":"absence","duration":"10m"}}`,
		`{"schema_version":1,"name":"Spike","scope":{"type":"device_type","device_type_id":"t1"},
		  "condition":{"type":"aggregate","function":"delta","field":"temperature","window":"60s","operator":"gte","value":10}}`,
		`{"schema_version":1,"name":"Hysteresis","cooldown":"15m","scope":{"type":"organization"},
		  "condition":{"type":"comparison","field":"temperature","operator":"gt","value":80},
		  "resolve":{"type":"comparison","field":"temperature","operator":"lt","value":70},
		  "actions":[{"type":"alert"},{"type":"webhook","on":["firing","resolved"],"integration_id":"hook-1"}]}`,
		`{"schema_version":1,"name":"Flapping","scope":{"type":"organization"},
		  "condition":{"type":"count_within","window":"10m","min_count":5,"condition":{"type":"comparison","field":"cpu_percent","operator":"gt","value":90}}}`,
		`{"schema_version":1,"name":"Nested","scope":{"type":"organization"},
		  "condition":{"type":"all","conditions":[
		    {"type":"any","conditions":[{"type":"comparison","field":"a","operator":"in","values":["x","y"]},{"type":"comparison","field":"b","operator":"between","min":1,"max":2}]},
		    {"type":"not","condition":{"type":"comparison","field":"c","operator":"exists"}}]}}`,
	}
	for i, r := range rules {
		if err := mustValidate(t, r, false); err != nil {
			t.Errorf("rule %d: %v", i, err)
		}
	}
}

func TestInvalidRulesListEveryProblem(t *testing.T) {
	cases := map[string]struct {
		raw   string
		paths []string
	}{
		"unknown field": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1},"script":"rm -rf"}`, []string{""}},
		"bad version":   {`{"schema_version":2,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1}}`, []string{"schema_version"}},
		"several": {`{"schema_version":1,"name":"","scope":{"type":"group"},"severity":"panic",
			"condition":{"type":"comparison","field":"a b","operator":"gt","value":"high"}}`,
			[]string{"name", "severity", "scope.group_id", "condition.field", "condition.value"}},
		"unknown node":    {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"javascript"}}`, []string{"condition.type"}},
		"window too long": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"absence","duration":"48h"}}`, []string{"condition.duration"}},
		"misplaced":       {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1,"window":"5m"}}`, []string{"condition.window"}},
		"between": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"between","min":5,"max":1}}`,
			[]string{"condition.min"}},
		"platform not allowed": {`{"schema_version":1,"name":"x","scope":{"type":"platform"},"condition":{"type":"absence","duration":"5m"}}`, []string{"scope.type"}},
		"absence in resolve": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1},
			"resolve":{"type":"absence","duration":"5m"}}`, []string{"resolve"}},
		"two alert actions": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1},
			"actions":[{"type":"alert"},{"type":"alert"}]}`, []string{"actions"}},
		"webhook without integration": {`{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1},
			"actions":[{"type":"webhook"}]}`, []string{"actions[0].integration_id"}},
	}
	for name, c := range cases {
		err := mustValidate(t, c.raw, false)
		var ve *ValidationError
		if !errors.As(err, &ve) {
			t.Errorf("%s: want validation error, got %v", name, err)
			continue
		}
		got := map[string]bool{}
		for _, p := range ve.Problems {
			got[p.Path] = true
		}
		for _, p := range c.paths {
			if !got[p] {
				t.Errorf("%s: missing problem at %q in %v", name, p, ve.Problems)
			}
		}
	}
}

func TestDepthAndNodeLimits(t *testing.T) {
	leaf := `{"type":"comparison","field":"a","operator":"gt","value":1}`
	deep := leaf
	for i := 0; i < 9; i++ {
		deep = `{"type":"not","condition":` + deep + `}`
	}
	err := mustValidate(t, `{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":`+deep+`}`, false)
	if err == nil || !strings.Contains(err.Error(), "nested deeper") {
		t.Fatalf("depth: %v", err)
	}
	wide := make([]string, 20)
	for i := range wide {
		wide[i] = `{"type":"all","conditions":[` + leaf + `,` + leaf + `,` + leaf + `]}`
	}
	err = mustValidate(t, `{"schema_version":1,"name":"x","scope":{"type":"organization"},"condition":{"type":"any","conditions":[`+strings.Join(wide, ",")+`]}}`, false)
	if err == nil || !strings.Contains(err.Error(), "more than 64") {
		t.Fatalf("nodes: %v", err)
	}
}

func TestConditionHashIgnoresPresentation(t *testing.T) {
	a, _ := Parse([]byte(`{"schema_version":1,"name":"A","severity":"info","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1}}`))
	b, _ := Parse([]byte(`{"schema_version":1,"name":"B","severity":"critical","cooldown":"5m","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":1},"actions":[{"type":"platform_event"}]}`))
	c, _ := Parse([]byte(`{"schema_version":1,"name":"A","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"gt","value":2}}`))
	if ConditionHash(a) != ConditionHash(b) {
		t.Fatal("presentation change must keep the hash")
	}
	if ConditionHash(a) == ConditionHash(c) {
		t.Fatal("condition change must change the hash")
	}
}

func TestEffectiveDefaults(t *testing.T) {
	r, _ := Parse([]byte(`{"schema_version":1,"name":"A","scope":{"type":"organization"},"condition":{"type":"absence","duration":"1m"},"actions":[{"type":"webhook","integration_id":"h"}]}`))
	if r.EffectiveSeverity() != SeverityWarning || !r.IsEnabled() || r.Streams()[0] != DefaultStream {
		t.Fatal("defaults")
	}
	acts := r.EffectiveActions()
	if len(acts) != 1 || acts[0].On[0] != OnFiring || len(acts[0].On) != 1 {
		t.Fatalf("webhook defaults to firing only: %+v", acts)
	}
	if m, n := r.Mode(); m != ModeSingle || n != 1 {
		t.Fatal("single mode by default")
	}
}
