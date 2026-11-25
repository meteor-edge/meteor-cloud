package deploy

import (
	"testing"

	"meteorcloud/meteorcli/internal/helper"
	wl "meteorcloud/meteorcli/internal/workload"
)

func TestStoppedByUserStaysStopped(t *testing.T) {
	e := newEnv(t)
	e.desire(dockerItem(rel1, "1.0.0", 1, map[string]any{"image": "demo:1"}))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync {
		t.Fatalf("report %v", got)
	}
	var run helper.DockerRunRequest
	e.host.last(t, "docker.run", &run)
	if run.Workload != "demo-app" || run.Release != rel1 {
		t.Errorf("labels %+v", run)
	}
	stops := wl.Stops{Store: e.d.State}
	if err := stops.Set("demo-app", wl.Stop{All: true}); err != nil {
		t.Fatal(err)
	}
	// Something started it: stopped again, not unhealthy, not installed again.
	got := e.cycle()["demo-app"]
	if got["stopped_by_user"] != true || got.health() != "unknown" || got.status() != statusInSync || e.host.count("docker.run") != 1 {
		t.Errorf("report %v", got)
	}
	var c helper.WorkloadControlRequest
	e.host.last(t, "workload.control", &c)
	if c.Verb != "stop" || c.Name != "demo-app" {
		t.Errorf("control %+v", c)
	}
	// Down, even removed: left alone.
	delete(e.host.containers, "demo-app")
	n := e.host.count("workload.control")
	if got := e.cycle()["demo-app"]; got["stopped_by_user"] != true || e.host.count("docker.run") != 1 || e.host.count("workload.control") != n {
		t.Errorf("report %v", got)
	}
	// The record survives an agent restart (a new module on the same state).
	m2 := &Module{Helper: e.host, clock: e.m.clock, probe: e.m.probe}
	m2.setup(e.d)
	e.m = m2
	if got := e.cycle()["demo-app"]; got["stopped_by_user"] != true {
		t.Errorf("after a restart: %v", got)
	}
	// A new deploy starts it and clears the record.
	e.desire(dockerItem(rel2, "2.0.0", 2, map[string]any{"image": "demo:2"}))
	got = e.cycle()["demo-app"]
	if got["stopped_by_user"] != nil || got.health() != "healthy" || stops.Get("demo-app").Any() || e.host.count("docker.run") != 2 {
		t.Errorf("after a deploy: %v, %+v", got, stops.Get("demo-app"))
	}
}

func TestStillRunning(t *testing.T) {
	st := helper.WorkloadState{Containers: []helper.WorkloadContainer{{Service: "web", Running: true}, {Service: "db"}}}
	if got := StillRunning(st, wl.Stop{Services: []string{"web", "db"}}); len(got) != 1 || got[0] != "web" {
		t.Errorf("services %v", got)
	}
	if got := StillRunning(st, wl.Stop{All: true}); len(got) != 1 || got[0] != "" {
		t.Errorf("all %v", got)
	}
	unit := helper.WorkloadState{Unit: &helper.WorkloadUnit{ActiveState: "active"}}
	if got := StillRunning(unit, wl.Stop{All: true}); len(got) != 1 {
		t.Errorf("unit %v", got)
	}
}

func TestInstalledWorkloads(t *testing.T) {
	e := newEnv(t)
	e.desire(dockerItem(rel1, "1.0.0", 1, map[string]any{"image": "demo:1", "container_name": "other"}))
	e.cycle()
	all, err := InstalledWorkloads(e.d.State)
	if err != nil || all["demo-app"] != (Installed{Key: "demo-app", Kind: "docker", Name: "other", ReleaseID: rel1, Version: "1.0.0", Started: true}) {
		t.Errorf("%+v, %v", all, err)
	}
}

// A Start holds the lock while it runs: a cycle that saw the stop record just
// before must not stop the workload after it.
func TestStopAgainLeavesARunningActionAlone(t *testing.T) {
	e := newEnv(t)
	e.d.Workloads = wl.NewLock()
	e.m = &Module{Helper: e.host, clock: e.m.clock, probe: e.m.probe}
	e.m.setup(e.d)
	e.desire(dockerItem(rel1, "1.0.0", 1, map[string]any{"image": "demo:1"}))
	e.cycle()
	if err := (wl.Stops{Store: e.d.State}).Set("demo-app", wl.Stop{All: true}); err != nil {
		t.Fatal(err)
	}
	release, ok := e.d.Workloads.TryAcquire()
	if !ok {
		t.Fatal("the lock is not free")
	}
	if got := e.cycle()["demo-app"]; got["stopped_by_user"] != true || e.host.count("workload.control") != 0 {
		t.Errorf("stopped during an action: %v", got)
	}
	release()
	if e.cycle(); e.host.count("workload.control") != 1 {
		t.Error("not stopped once the action finished")
	}
}
