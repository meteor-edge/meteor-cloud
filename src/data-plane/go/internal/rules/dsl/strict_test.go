package dsl

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The same fixture runs against the schema and the API's validator
// (src/control-plane/tests/test_rules.py).
func TestContractRejectionsAreRefused(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", "..", "contracts", "rules-v1.invalid.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Reason string          `json:"reason"`
		Rule   json.RawMessage `json:"rule"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) == 0 {
		t.Fatal(err, len(cases))
	}
	for _, c := range cases {
		if _, err := ParseAndValidate(c.Rule, DefaultLimits(), false); err == nil {
			t.Errorf("accepted: %s", c.Reason)
		}
	}
}

func parse(t *testing.T, extra string) (*Rule, error) {
	t.Helper()
	return ParseAndValidate([]byte(`{"schema_version":1,"name":"x","condition":{"type":"comparison","field":"a","operator":"exists"},`+extra+`}`), DefaultLimits(), false)
}

func TestLengthsCountCharacters(t *testing.T) {
	e := func(n int) string { return strings.Repeat("é", n) }
	for n, ok := range map[int]bool{256: true, 257: false} {
		_, err := parse(t, `"scope":{"type":"organization","labels":{"`+e(64)+`":"`+e(n)+`"}},"description":"`+e(2000)+`"`)
		if (err == nil) != ok {
			t.Errorf("%d: %v", n, err)
		}
	}
	for n, ok := range map[int]bool{200: true, 201: false} {
		_, err := ParseAndValidate([]byte(`{"schema_version":1,"name":"`+e(n)+`","scope":{"type":"organization"},"condition":{"type":"comparison","field":"a","operator":"eq","value":"`+e(256)+`"}}`), DefaultLimits(), false)
		if (err == nil) != ok {
			t.Errorf("%d: %v", n, err)
		}
	}
}

func TestParamsAreMeasuredCompact(t *testing.T) {
	for n, ok := range map[int]bool{4088: true, 4089: false} {
		_, err := parse(t, `"scope":{"type":"organization"},"actions":[{"type":"platform_event","params":{"x" : "`+strings.Repeat("a", n)+`"}}]`)
		if (err == nil) != ok {
			t.Errorf("%d: %v", n, err)
		}
	}
}

func TestConditionHashTreatsListsAsSets(t *testing.T) {
	hash := func(extra string) string {
		r, err := parse(t, extra)
		if err != nil {
			t.Fatal(err)
		}
		return ConditionHash(r)
	}
	a := hash(`"scope":{"type":"device","device_ids":["a","b"]},"source":{"streams":["s1","s2"]}`)
	if a != hash(`"scope":{"type":"device","device_ids":["b","a"]},"source":{"streams":["s2","s1"]}`) {
		t.Error("order changed the hash")
	}
	if a == hash(`"scope":{"type":"device","device_ids":["a"]},"source":{"streams":["s1","s2"]}`) ||
		a == hash(`"scope":{"type":"device","device_ids":["a","b"]},"source":{"streams":["s1"]}`) {
		t.Error("another set has the same hash")
	}
}
