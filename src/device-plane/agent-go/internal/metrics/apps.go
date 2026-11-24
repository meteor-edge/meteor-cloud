package metrics

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"math"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/helper"
)

// Limits ported from the Python observer.
const (
	maxApps = helper.MaxContainers
	// maxAppName keeps "app." + name + ".health_ms" within the data plane's
	// 64-character metric keys.
	maxAppName        = 50
	crashLoopRestarts = 3
	crashLoopWindow   = 10 * time.Minute
)

// appState is one container as docker inspect reports it.
type appState struct {
	name      string
	status    string
	running   bool
	exitCode  *int
	oomKilled bool
	restarts  int
	startedAt string
	health    string
	healthMS  *float64
	// workload is the MeteorCloud workload the container belongs to, "" for none.
	workload string
}

// apps watches the Docker containers once per metrics interval and reports what
// changed since the last look. The first look sets the baseline (no events).
type apps struct {
	helper caller
	// composeDir tells MeteorCloud's compose projects from others.
	composeDir string
	now        func() time.Time
	previous   map[string]appState
	restarts   map[string][]time.Time // restart times in the crash-loop window
	looping    map[string]bool
}

func newApps(h caller) *apps {
	return &apps{helper: h, now: time.Now, restarts: map[string][]time.Time{}, looping: map[string]bool{}}
}

// observe returns the app.<name>.<field> metrics and the lifecycle events. A
// failed look returns nothing and keeps the previous baseline, so a docker
// hiccup does not report every app removed.
func (a *apps) observe(ctx context.Context) (map[string]any, []map[string]any) {
	states, ok := a.states(ctx)
	if !ok {
		return nil, nil
	}
	anyRunning := false
	for _, s := range states {
		anyRunning = anyRunning || s.running
	}
	stats := map[string][2]*float64{}
	if anyRunning {
		stats = a.stats(ctx)
	}
	m := map[string]any{}
	for name, s := range states {
		prefix := "app." + metricName(name) + "."
		m[prefix+"up"] = boolInt(s.running)
		m[prefix+"restarts"] = s.restarts
		if s.health == "healthy" || s.health == "unhealthy" {
			m[prefix+"healthy"] = boolInt(s.health == "healthy")
		}
		if s.healthMS != nil {
			m[prefix+"health_ms"] = *s.healthMS
		}
		if st, ok := stats[name]; ok {
			if st[0] != nil {
				m[prefix+"cpu"] = *st[0]
			}
			if st[1] != nil {
				m[prefix+"mem"] = int64(math.Round(*st[1]))
			}
		}
	}
	var events []map[string]any
	if a.previous != nil {
		events = a.changes(a.previous, states)
	}
	a.previous = states
	return m, events
}

func (a *apps) states(ctx context.Context) (map[string]appState, bool) {
	var ps helper.DockerOutput
	if err := a.helper.Call(ctx, "docker.ps", nil, &ps); err != nil || !ps.OK {
		return nil, false
	}
	var names []string
	for _, n := range strings.Fields(ps.Output) {
		if len(names) < maxApps {
			names = append(names, n)
		}
	}
	states := map[string]appState{}
	if len(names) == 0 {
		return states, true
	}
	var out helper.DockerOutput
	if err := a.helper.Call(ctx, "docker.inspect", helper.DockerInspectRequest{Names: names}, &out); err != nil || strings.TrimSpace(out.Output) == "" {
		return nil, false
	}
	for _, line := range strings.Split(out.Output, "\n") {
		if s, ok := parseInspect(line, a.composeDir); ok {
			states[s.name] = s
		}
	}
	return states, true
}

func (a *apps) stats(ctx context.Context) map[string][2]*float64 {
	var out helper.DockerOutput
	if err := a.helper.Call(ctx, "docker.stats", nil, &out); err != nil || !out.OK {
		return map[string][2]*float64{}
	}
	return parseStats(out.Output)
}

// changes compares two looks (the Python observer's rules).
func (a *apps) changes(before, now map[string]appState) []map[string]any {
	var events []map[string]any
	at := a.now()
	add := func(kind, app string, kv ...any) {
		e := map[string]any{"type": kind, "app": app}
		for i := 0; i+1 < len(kv); i += 2 {
			e[kv[i].(string)] = kv[i+1]
		}
		s, ok := now[app]
		if !ok {
			s = before[app]
		}
		if s.workload != "" {
			e["workload_key"] = s.workload
		}
		events = append(events, e)
	}
	for _, name := range sortedKeys(now) {
		s := now[name]
		old, seen := before[name]
		if !seen {
			if s.running {
				add("app.started", name)
			}
			continue
		}
		switch {
		case old.running && !s.running:
			add("app.exited", name, "exit_code", s.exitCode, "oom_killed", s.oomKilled, "detail", s.status)
		case s.running && (!old.running || s.startedAt != old.startedAt):
			if s.restarts > old.restarts {
				add("app.restarted", name, "restarts", s.restarts)
			} else {
				add("app.started", name)
			}
		case s.restarts > old.restarts:
			add("app.restarted", name, "restarts", s.restarts)
		}
		if s.oomKilled && !old.oomKilled {
			add("app.oom_killed", name, "exit_code", s.exitCode)
		}
		if s.health != old.health && (s.health == "healthy" || s.health == "unhealthy") {
			add("app."+s.health, name, "health_ms", s.healthMS)
		}
		// Crash loop: several restarts inside the window, reported once until it calms down.
		history := a.restarts[name]
		for i := 0; i < min(max(s.restarts-old.restarts, 0), 10); i++ {
			history = append(history, at)
		}
		for len(history) > 0 && at.Sub(history[0]) > crashLoopWindow {
			history = history[1:]
		}
		a.restarts[name] = history
		if len(history) >= crashLoopRestarts || (s.status == "restarting" && len(history) > 1) {
			if !a.looping[name] {
				a.looping[name] = true
				add("app.crash_loop", name, "restarts", s.restarts)
			}
		} else if len(history) == 0 {
			delete(a.looping, name)
		}
	}
	for _, name := range sortedKeys(before) {
		if _, ok := now[name]; !ok {
			add("app.removed", name)
			delete(a.restarts, name)
			delete(a.looping, name)
		}
	}
	return events
}

// AppName is how a container is called in metric keys (app.<name>.<field>).
func AppName(container string) string { return metricName(container) }

// parseInspect reads one line of helper.InspectTemplate. composeDir is where
// the deploy module keeps its compose projects.
func parseInspect(line, composeDir string) (appState, bool) {
	fields := append(strings.Split(strings.Trim(line, " \r\n"), "\t"), "", "", "")
	line = fields[0]
	parts := strings.Split(strings.TrimSpace(line), "|")
	if len(parts) < 7 {
		return appState{}, false
	}
	name := strings.TrimPrefix(parts[0], "/")
	if name == "" {
		return appState{}, false
	}
	s := appState{
		name: name, status: parts[1],
		running:   strings.EqualFold(parts[2], "true") && parts[1] != "restarting",
		oomKilled: strings.EqualFold(parts[4], "true"),
		startedAt: parts[6],
	}
	if n, err := strconv.Atoi(parts[3]); err == nil {
		s.exitCode = &n
	}
	s.workload = fields[1]
	if s.workload == "" && fields[2] != "" && composeDir != "" && filepath.Dir(fields[3]) == composeDir {
		s.workload = fields[2]
	}
	s.restarts, _ = strconv.Atoi(parts[5])
	if len(parts) >= 8 && parts[7] != "" {
		s.health = parts[7]
		var checks []string
		if len(parts) > 8 {
			for _, c := range strings.Split(parts[8], ";") {
				if c != "" {
					checks = append(checks, c)
				}
			}
		}
		if len(checks) > 0 {
			start, end, ok := strings.Cut(checks[len(checks)-1], ",")
			a, err1 := strconv.ParseInt(start, 10, 64)
			b, err2 := strconv.ParseInt(end, 10, 64)
			if ok && err1 == nil && err2 == nil && b > a {
				ms := math.Round(float64(b-a)/1e5) / 10
				s.healthMS = &ms
			}
		}
	}
	return s, true
}

// parseStats reads "name|12.5%|45.6MiB / 1.9GiB" lines into {name: [cpu %, memory bytes]}.
func parseStats(out string) map[string][2]*float64 {
	stats := map[string][2]*float64{}
	for _, line := range strings.Split(out, "\n") {
		parts := strings.Split(strings.TrimSpace(line), "|")
		if len(parts) < 3 {
			continue
		}
		mem, _, _ := strings.Cut(parts[2], "/")
		stats[strings.TrimPrefix(parts[0], "/")] = [2]*float64{parsePercent(parts[1]), parseBytes(mem)}
	}
	return stats
}

func parsePercent(v string) *float64 {
	f, err := strconv.ParseFloat(strings.TrimSuffix(strings.TrimSpace(v), "%"), 64)
	if err != nil {
		return nil
	}
	f = math.Round(f*100) / 100
	return &f
}

// units are docker's size suffixes, longest first so "mib" wins over "b".
var units = []struct {
	suffix string
	factor float64
}{
	{"kib", 1024}, {"mib", 1 << 20}, {"gib", 1 << 30}, {"tib", 1 << 40},
	{"kb", 1e3}, {"mb", 1e6}, {"gb", 1e9}, {"b", 1},
}

func parseBytes(v string) *float64 {
	text := strings.ToLower(strings.TrimSpace(v))
	for _, u := range units {
		if strings.HasSuffix(text, u.suffix) {
			f, err := strconv.ParseFloat(strings.TrimSpace(strings.TrimSuffix(text, u.suffix)), 64)
			if err != nil {
				return nil
			}
			f *= u.factor
			return &f
		}
	}
	return nil
}

// metricName is the app's name in metric keys: as is up to maxAppName, else a
// prefix plus a hash, so two long names that share a prefix still differ.
func metricName(name string) string {
	if len(name) <= maxAppName {
		return name
	}
	sum := sha1.Sum([]byte(name))
	return name[:maxAppName-9] + "~" + hex.EncodeToString(sum[:])[:8]
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
