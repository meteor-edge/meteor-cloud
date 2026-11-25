package runtime

import (
	"encoding/json"
	"testing"
)

func TestSpecGetters(t *testing.T) {
	s := Spec{
		"name": "x", "empty": "", "null": nil, "zero": json.Number("0"), "n": json.Number("8080"), "f": 3.0,
		"off": false, "list": []any{"a", json.Number("1")}, "env": map[string]any{"A": "1", "B": true, "C": nil},
	}
	for _, c := range []struct{ key, want string }{
		{"name", "x"}, {"empty", "def"}, {"null", "def"}, {"zero", "def"}, {"n", "8080"}, {"f", "3"}, {"off", "def"}, {"missing", "def"},
	} {
		if got := s.Text(c.key, "def"); got != c.want {
			t.Errorf("Text(%s) = %q, want %q", c.key, got, c.want)
		}
	}
	if !s.Bool("missing", true) || s.Bool("off", true) || s.Bool("null", true) || !s.Bool("name", false) || s.Bool("zero", true) {
		t.Error("Bool does not follow Python truthiness")
	}
	if l := s.Strings("list"); len(l) != 2 || l[1] != "1" {
		t.Errorf("Strings %q", l)
	}
	if e := s.Env("env"); e["A"] != "1" || e["B"] != "True" || e["C"] != "None" {
		t.Errorf("Env %v", e)
	}
}

func TestBoundEnv(t *testing.T) {
	app := &App{Env: map[string]string{"METEORCLOUD_ENDPOINT": "https://cp", "METEORCLOUD_APP_ID": "a"}}
	if got := BoundEnv(map[string]string{"X": "1"}, app); got["METEORCLOUD_ENDPOINT"] != "https://cp" || got["X"] != "1" {
		t.Errorf("BoundEnv %v", got)
	}
	if got := BoundEnv(map[string]string{"METEORCLOUD_ENDPOINT": "https://own"}, app); got["METEORCLOUD_ENDPOINT"] != "https://own" {
		t.Errorf("a release's own endpoint was replaced: %v", got)
	}
	if got := BoundEnv(nil, nil); got == nil || len(got) != 0 {
		t.Errorf("BoundEnv(nil, nil) = %v", got)
	}
}

func TestCheckName(t *testing.T) {
	for _, bad := range []string{"", "  ", "a/b", ".hidden"} {
		if _, err := CheckName("unit_name", bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	if n, err := CheckName("unit_name", " ok.service "); err != nil || n != "ok.service" {
		t.Errorf("CheckName = %q, %v", n, err)
	}
}
