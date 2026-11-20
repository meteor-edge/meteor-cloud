package metrics

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/bus"
)

func writeFile(t *testing.T, root, name, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(root, name), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func settingsFor(host, port, ca string) bus.Settings {
	p, _ := strconv.Atoi(port)
	return bus.Settings{DeviceID: "d-1", Host: host, Port: p, Username: "u", Password: "p", CAFile: ca, Will: `{"status":"offline"}`}
}

func moduleWith(m *Module, h caller) *Module {
	m.helper = h
	return m
}

func intp(n int) *int { return &n }

func TestAppChanges(t *testing.T) {
	run := func(name string, status string, restarts int, started string) appState {
		return appState{name: name, status: status, running: status == "running", restarts: restarts, startedAt: started, exitCode: intp(0)}
	}
	tests := []struct {
		name   string
		before map[string]appState
		now    map[string]appState
		want   []string
	}{
		{"new running app", map[string]appState{}, map[string]appState{"a": run("a", "running", 0, "t1")}, []string{"app.started"}},
		{"new stopped app", map[string]appState{}, map[string]appState{"a": run("a", "exited", 0, "t1")}, nil},
		{"exited", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{"a": run("a", "exited", 0, "t1")}, []string{"app.exited"}},
		{"restarted by docker", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{"a": run("a", "running", 1, "t2")}, []string{"app.restarted"}},
		{"started again by hand", map[string]appState{"a": run("a", "exited", 0, "t1")}, map[string]appState{"a": run("a", "running", 0, "t2")}, []string{"app.started"}},
		{"restart counted while down", map[string]appState{"a": run("a", "exited", 0, "t1")}, map[string]appState{"a": run("a", "exited", 1, "t1")}, []string{"app.restarted"}},
		{"removed", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{}, []string{"app.removed"}},
		{"oom", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{"a": {name: "a", status: "exited", oomKilled: true, startedAt: "t1"}}, []string{"app.exited", "app.oom_killed"}},
		{"unhealthy", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{"a": {name: "a", status: "running", running: true, startedAt: "t1", health: "unhealthy"}}, []string{"app.unhealthy"}},
		{"crash loop", map[string]appState{"a": run("a", "running", 0, "t1")}, map[string]appState{"a": run("a", "running", 3, "t2")}, []string{"app.restarted", "app.crash_loop"}},
	}
	for _, tt := range tests {
		a := newApps(nil)
		var got []string
		for _, e := range a.changes(tt.before, tt.now) {
			got = append(got, e["type"].(string))
		}
		if strings.Join(got, ",") != strings.Join(tt.want, ",") {
			t.Errorf("%s: events %v, want %v", tt.name, got, tt.want)
		}
	}
}

func TestCrashLoopReportedOnceUntilCalm(t *testing.T) {
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	a := newApps(nil)
	a.now = func() time.Time { return now }
	st := func(restarts int) map[string]appState {
		return map[string]appState{"a": {name: "a", status: "running", running: true, restarts: restarts, startedAt: strconv.Itoa(restarts)}}
	}
	count := func(events []map[string]any) int {
		n := 0
		for _, e := range events {
			if e["type"] == "app.crash_loop" {
				n++
			}
		}
		return n
	}
	if n := count(a.changes(st(0), st(3))); n != 1 {
		t.Fatalf("crash loops = %d", n)
	}
	if n := count(a.changes(st(3), st(4))); n != 0 {
		t.Error("crash loop reported twice")
	}
	now = now.Add(11 * time.Minute)
	a.changes(st(4), st(4)) // calm: history empties
	if n := count(a.changes(st(4), st(7))); n != 1 {
		t.Error("a new crash loop after a calm period must be reported")
	}
}

func TestParsers(t *testing.T) {
	s, ok := parseInspect("/web|running|true|0|false|2|2026-01-01T00:00:00Z|healthy|1000000,3500000;5000000,7500000;")
	if !ok || s.name != "web" || !s.running || s.restarts != 2 || s.health != "healthy" || *s.healthMS != 2.5 || *s.exitCode != 0 {
		t.Errorf("inspect = %+v", s)
	}
	if s, _ := parseInspect("/r|restarting|true|1|false|5|t|"); s.running {
		t.Error("a restarting container is not running")
	}
	if _, ok := parseInspect("short|line"); ok {
		t.Error("short line parsed")
	}
	st := parseStats("web|12.345%|45.5MiB / 1.9GiB\nbad\ndb|--|1kB / 2kB\n")
	if *st["web"][0] != 12.35 || *st["web"][1] != 45.5*(1<<20) {
		t.Errorf("web stats = %v %v", *st["web"][0], *st["web"][1])
	}
	if st["db"][0] != nil || *st["db"][1] != 1000 {
		t.Errorf("db stats = %v", st["db"])
	}
	long := strings.Repeat("x", 60)
	if got := metricName(long); len(got) != maxAppName || got == metricName(long+"y") {
		t.Errorf("metricName = %q", got)
	}
	if metricName("short") != "short" {
		t.Error("short names stay as they are")
	}
}
