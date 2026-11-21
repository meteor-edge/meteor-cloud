package deploy

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/state"
)

func TestGate(t *testing.T) {
	healthy := runtime.Health{Status: runtime.Healthy}
	down := runtime.Health{Status: runtime.Unhealthy, Problem: "container x is not running"}
	starting := runtime.Health{Status: runtime.Unknown, Problem: "starting"}
	tests := []struct {
		name   string
		gate   gate
		checks []runtime.Health // one per poll; the last repeats
		probe  string
		want   string
	}{
		{name: "healthy and stable", gate: gate{Timeout: 10 * time.Second, Stable: 4 * time.Second}, checks: []runtime.Health{healthy}},
		{name: "comes up after starting", gate: gate{Timeout: 10 * time.Second, Stable: 2 * time.Second},
			checks: []runtime.Health{starting, starting, healthy}},
		{name: "never healthy", gate: gate{Timeout: 6 * time.Second}, checks: []runtime.Health{down}, want: "container x is not running"},
		{name: "starting forever", gate: gate{Timeout: 4 * time.Second}, checks: []runtime.Health{starting}, want: "starting"},
		{name: "flaps inside the stable window", gate: gate{Timeout: 4 * time.Second, Stable: 4 * time.Second},
			checks: []runtime.Health{healthy, down, healthy, down}, want: "container x is not running"},
		{name: "url does not answer", gate: gate{Timeout: 4 * time.Second, URL: "http://x/health"}, checks: []runtime.Health{healthy},
			probe: "http://x/health answered HTTP 500", want: "http://x/health answered HTTP 500"},
		{name: "no problem text", gate: gate{Timeout: 2 * time.Second}, checks: []runtime.Health{{Status: runtime.Unhealthy}},
			want: "not healthy within 2s"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			i := 0
			check := func(context.Context) runtime.Health {
				h := tc.checks[min(i, len(tc.checks)-1)]
				i++
				return h
			}
			probe := func(context.Context, string) string { return tc.probe }
			if got := tc.gate.wait(context.Background(), check, probe, &fakeClock{now: time.Unix(0, 0)}); got != tc.want {
				t.Fatalf("wait = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestGateOf(t *testing.T) {
	if gateOf(runtime.Spec{}) != nil {
		t.Fatal("no health_check, no gate")
	}
	g := gateOf(runtime.Spec{"health_check": map[string]any{}})
	if g.Timeout != 60*time.Second || g.Stable != 10*time.Second || !g.Rollback || g.URL != "" {
		t.Fatalf("defaults %+v", g)
	}
	g = gateOf(runtime.Spec{"health_check": map[string]any{
		"timeout_seconds": json.Number("30"), "stable_seconds": json.Number("0"), "rollback": false, "http_url": "http://x",
	}})
	if g.Timeout != 30*time.Second || g.Stable != 0 || g.Rollback || g.URL != "http://x" {
		t.Fatalf("settings %+v", g)
	}
}

func TestHTTPProbe(t *testing.T) {
	if got := httpProbe(context.Background(), "http://127.0.0.1:1/health"); got == "" {
		t.Fatal("a closed port answered")
	}
}

func newApplied(t *testing.T) (appliedState, string) {
	t.Helper()
	dir := t.TempDir()
	st, err := state.NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	return appliedState{store: st}, dir
}

func TestAppliedStateReadsThePythonFile(t *testing.T) {
	a, dir := newApplied(t)
	py := `{"demo-app": {"release_id": "r1", "revision": 3, "item": {"workload_key": "demo-app", "kind": "docker",
		"release_id": "r1", "revision": 3, "spec": {"image": "nginx"}}},
		"other": {"release_id": "r9", "revision": 1, "reapply": true}}`
	if err := os.WriteFile(filepath.Join(dir, appliedFile), []byte(py), 0o600); err != nil {
		t.Fatal(err)
	}
	all, err := a.load()
	if err != nil {
		t.Fatal(err)
	}
	if all["demo-app"].Revision != 3 || all["demo-app"].Item.spec().Text("image", "") != "nginx" || !all["other"].Reapply {
		t.Fatalf("loaded %+v", all)
	}
	// Writing converts it, without keeping the old top-level copies.
	if err := a.save(record{key: "demo-app", release: "r2", revision: 4}); err != nil {
		t.Fatal(err)
	}
	var raw map[string]json.RawMessage
	data, _ := os.ReadFile(filepath.Join(dir, appliedFile))
	_ = json.Unmarshal(data, &raw)
	if _, stale := raw["demo-app"]; stale || string(raw["version"]) != "1" {
		t.Fatalf("file after write: %s", data)
	}
	all, _ = a.load()
	if all["demo-app"].ReleaseID != "r2" || all["demo-app"].Item == nil || all["other"].ReleaseID != "r9" {
		t.Fatalf("after write %+v", all)
	}
}

func TestAppliedRevisionIsAHighWaterMark(t *testing.T) {
	a, _ := newApplied(t)
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(a.save(record{key: "w", release: "r5", revision: 5, item: item{"release_id": "r5"}}))
	must(a.save(record{key: "w", release: "r4", revision: 4}))
	all, _ := a.load()
	if all["w"].Revision != 5 || all["w"].ReleaseID != "r5" {
		t.Fatalf("lowered without a rollback: %+v", all["w"])
	}
	must(a.save(record{key: "w", release: "r4", revision: 4, rolledBackFrom: &failedRelease{ReleaseID: "r5", Revision: 5}}))
	all, _ = a.load()
	if all["w"].Revision != 4 || all["w"].RolledBackFrom == nil {
		t.Fatalf("rollback not recorded: %+v", all["w"])
	}
}

func TestAppliedStateDamagedFileStartsClean(t *testing.T) {
	a, dir := newApplied(t)
	_ = os.WriteFile(filepath.Join(dir, appliedFile), []byte("{not json"), 0o600)
	all, err := a.load()
	if err != nil || len(all) != 0 {
		t.Fatalf("load = %v, %v", all, err)
	}
	matches, _ := filepath.Glob(filepath.Join(dir, appliedFile+".corrupt-*"))
	if len(matches) != 1 {
		t.Fatalf("damaged file not moved aside: %v", matches)
	}
}

func TestCachedArtifact(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "a1")
	_ = os.WriteFile(path, tarball, 0o640)
	if cached(path, "") {
		t.Fatal("cached without a checksum file")
	}
	_ = os.WriteFile(path+".sha256", []byte(sha(tarball)+"\n"), 0o640)
	if !cached(path, "") || !cached(path, sha(tarball)) {
		t.Fatal("not cached")
	}
	if cached(path, sha([]byte("other"))) {
		t.Fatal("cached with another checksum")
	}
	_ = os.WriteFile(path, []byte("tampered"), 0o640)
	if cached(path, sha(tarball)) || cached(path, "") {
		t.Fatal("a changed file counted as cached")
	}
}

func TestKindsMatchTheRegistry(t *testing.T) {
	if len(Kinds()) != len(kinds) {
		t.Fatalf("Kinds() %v, registry %d", Kinds(), len(kinds))
	}
	for _, k := range Kinds() {
		if _, ok := kinds[k]; !ok {
			t.Errorf("%s not in the registry", k)
		}
	}
}

func TestGateDoesNotCountASlowCheckAsStableTime(t *testing.T) {
	clk := &fakeClock{now: time.Unix(0, 0)}
	var at []time.Duration
	check := func(context.Context) runtime.Health {
		if len(at) == 0 {
			clk.now = clk.now.Add(40 * time.Second) // the first check hangs for 40 s
		}
		at = append(at, clk.now.Sub(time.Unix(0, 0)))
		return runtime.Health{Status: runtime.Healthy}
	}
	g := gate{Timeout: 60 * time.Second, Stable: 10 * time.Second}
	if why := g.wait(context.Background(), check, nil, clk); why != "" {
		t.Fatal(why)
	}
	if last := at[len(at)-1]; last < 50*time.Second {
		t.Fatalf("passed at %v, before 10 s of healthy observations (checks at %v)", last, at)
	}
}

func TestAppliedStateKeepsUnknownWorkloadFields(t *testing.T) {
	a, dir := newApplied(t)
	file := `{"version": 1, "workloads": {"w": {"release_id": "r1", "revision": 1, "future": {"x": 1}},
		"other": {"release_id": "r9", "revision": 2, "note": "kept"}}}`
	if err := os.WriteFile(filepath.Join(dir, appliedFile), []byte(file), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := a.save(record{key: "w", release: "r2", revision: 2}); err != nil {
		t.Fatal(err)
	}
	var got struct {
		Workloads map[string]map[string]json.RawMessage `json:"workloads"`
	}
	data, _ := os.ReadFile(filepath.Join(dir, appliedFile))
	_ = json.Unmarshal(data, &got)
	compact := func(raw json.RawMessage) string {
		var b bytes.Buffer
		_ = json.Compact(&b, raw)
		return b.String()
	}
	if compact(got.Workloads["w"]["future"]) != `{"x":1}` || compact(got.Workloads["other"]["note"]) != `"kept"` ||
		compact(got.Workloads["w"]["release_id"]) != `"r2"` {
		t.Fatalf("file after save: %s", data)
	}
}

func TestPythonAppliedStateStaysWhenTheMigrationFails(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	a, dir := newApplied(t)
	py := `{"demo-app": {"release_id": "r1", "revision": 3}}`
	path := filepath.Join(dir, appliedFile)
	if err := os.WriteFile(path, []byte(py), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(dir, 0o700)
	if err := a.save(record{key: "demo-app", release: "r2", revision: 4}); err == nil {
		t.Fatal("save succeeded in a read-only directory")
	}
	if data, _ := os.ReadFile(path); string(data) != py {
		t.Fatalf("Python file changed: %s", data)
	}
}
