package actions

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/workload"
)

const snapshotRoute = "/api/v1/agent/software/snapshots"

// workloadEnv is a device that runs compose project "shop" and container "web".
func workloadEnv(t *testing.T) (env, *fakeHelper, *fakecp.Server, workload.Stops) {
	t.Helper()
	cp := fakecp.New(t)
	cp.On("POST", snapshotRoute, fakecp.OK(map[string]any{"accepted": true}))
	d := agenttest.Deps(t, cp, nil, nil)
	item := func(key, kind string) map[string]any {
		return map[string]any{"release_id": "r1", "revision": 1,
			"item": map[string]any{"workload_key": key, "kind": kind, "release_id": "r1", "revision": 1, "spec": map[string]any{}}}
	}
	err := d.State.Write("applied-software.json", 1, struct {
		Workloads map[string]any `json:"workloads"`
	}{map[string]any{"shop": item("shop", "docker_compose"), "web": item("web", "docker")}})
	if err != nil {
		t.Fatal(err)
	}
	h := &fakeHelper{fail: map[string]error{}}
	r := &runner{d: d, helper: h, backoff: api.NewBackoff()}
	return r.env(), h, cp, workload.Stops{Store: d.State}
}

func act(key, service string) api.Action {
	a := api.Action{ID: "a1", WorkloadKey: &key}
	if service != "" {
		a.Service = &service
	}
	return a
}

func verb(t *testing.T, h *fakeHelper) string {
	t.Helper()
	for i := len(h.ops) - 1; i >= 0; i-- {
		if h.ops[i] == "workload.control" {
			var r helper.WorkloadControlRequest
			if err := json.Unmarshal([]byte(h.parms[i]), &r); err != nil {
				t.Fatal(err)
			}
			return strings.TrimSpace(r.Verb + " " + r.Name + " " + r.Service)
		}
	}
	return ""
}

func TestWorkloadActions(t *testing.T) {
	e, h, cp, stops := workloadEnv(t)
	ctx := context.Background()
	sub := e.d.Signals.Subscribe(agent.Is(agent.WorkWaiting{Kind: "software"}))
	defer sub.Close()
	run := func(kind, key, service string) error { return registry[kind].run(ctx, e, act(key, service)) }

	if err := run("workload_stop", "web", ""); err != nil {
		t.Fatal(err)
	}
	if !stops.Get("web").All || verb(t, h) != "stop web" {
		t.Errorf("after stop: %+v, %s", stops.Get("web"), verb(t, h))
	}
	if len(sub.Take()) != 1 || len(cp.Calls("POST", snapshotRoute)) != 1 {
		t.Error("no health report or snapshot after the stop")
	}
	// Restarting a stopped workload starts it.
	if err := run("workload_restart", "web", ""); err != nil || verb(t, h) != "start web" || stops.Get("web").Any() {
		t.Errorf("restart: %v, %s, %+v", err, verb(t, h), stops.Get("web"))
	}
	// One service.
	if err := run("workload_stop", "shop", "db"); err != nil || verb(t, h) != "stop shop db" || stops.Get("shop").All {
		t.Errorf("stop service: %v, %s, %+v", err, verb(t, h), stops.Get("shop"))
	}
	if err := run("workload_stop", "shop", ""); err != nil || !stops.Get("shop").All {
		t.Errorf("stop all: %v", err)
	}
	if err := run("workload_start", "shop", "db"); err == nil {
		t.Error("started one service of a stopped workload")
	}
	if err := run("workload_start", "shop", ""); err != nil || stops.Get("shop").Any() {
		t.Errorf("start: %v, %+v", err, stops.Get("shop"))
	}
	for _, bad := range [][2]string{{"unknown", ""}, {"web", "svc"}, {"", ""}} {
		if err := run("workload_restart", bad[0], bad[1]); err == nil {
			t.Errorf("accepted %v", bad)
		}
	}
	// A refused stop leaves no record.
	h.fail["workload.control"] = errors.New("refused")
	if err := run("workload_stop", "web", ""); err == nil || stops.Get("web").Any() {
		t.Errorf("failed stop: %v, %+v", err, stops.Get("web"))
	}
}

func TestWorkloadActionWaitsForADeploy(t *testing.T) {
	e, h, _, stops := workloadEnv(t)
	release, err := e.d.Workloads.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := registry["workload_stop"].run(ctx, e, act("web", "")); err == nil {
		t.Error("ran during a deploy")
	}
	if len(h.ops) != 0 || stops.Get("web").Any() {
		t.Errorf("ops %v, stops %+v", h.ops, stops.Get("web"))
	}
}
