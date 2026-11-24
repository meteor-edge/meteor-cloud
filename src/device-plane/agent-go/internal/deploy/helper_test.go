package deploy

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/signing"
	"meteorcloud/meteorcli/internal/state"
)

// fakeHost plays the root helper on a small simulated machine: containers,
// compose projects and units. Images in broken start but do not stay up.
type fakeHost struct {
	mu         sync.Mutex
	calls      []string
	params     map[string][]json.RawMessage
	containers map[string]string // name -> image
	projects   map[string]bool
	units      map[string]string // name -> content
	broken     map[string]bool   // images (or projects) that exit right away
	starting   bool              // HEALTHCHECK still starting
	block      chan struct{}     // when set, docker.run waits for it (a hung pull)
	fail       map[string]error
}

func newFakeHost() *fakeHost {
	return &fakeHost{
		params: map[string][]json.RawMessage{}, containers: map[string]string{}, projects: map[string]bool{},
		units: map[string]string{}, broken: map[string]bool{}, fail: map[string]error{},
	}
}

func (f *fakeHost) count(op string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.params[op])
}

func (f *fakeHost) last(t *testing.T, op string, v any) {
	t.Helper()
	f.mu.Lock()
	defer f.mu.Unlock()
	p := f.params[op]
	if len(p) == 0 {
		t.Fatalf("%s was not called (calls: %v)", op, f.calls)
	}
	if err := json.Unmarshal(p[len(p)-1], v); err != nil {
		t.Fatal(err)
	}
}

func (f *fakeHost) Call(ctx context.Context, op string, params, out any) error {
	raw, _ := json.Marshal(params)
	f.mu.Lock()
	f.calls = append(f.calls, op)
	f.params[op] = append(f.params[op], raw)
	block := f.block
	err := f.fail[op]
	f.mu.Unlock()
	if err != nil {
		return err
	}
	if op == "docker.run" && block != nil {
		select {
		case <-block:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	var result any
	switch op {
	case "docker.load":
		result = helper.DockerOutput{OK: true, Output: "Loaded image: demo/app:1.0\n"}
	case "docker.run":
		var r helper.DockerRunRequest
		_ = json.Unmarshal(raw, &r)
		f.containers[r.Name] = r.Image
		result = helper.DockerOutput{OK: true, Output: "0123\n"}
	case "docker.health":
		var r helper.DockerNameRequest
		_ = json.Unmarshal(raw, &r)
		image, ok := f.containers[r.Name]
		switch {
		case !ok:
			result = helper.DockerOutput{OK: false, Output: "Error: No such object"}
		case f.broken[image]:
			result = helper.DockerOutput{OK: true, Output: "false|exited|1|\n"}
		case f.starting:
			result = helper.DockerOutput{OK: true, Output: "true|running|0|starting\n"}
		default:
			result = helper.DockerOutput{OK: true, Output: "true|running|0|\n"}
		}
	case "docker.remove":
		var r helper.DockerNameRequest
		_ = json.Unmarshal(raw, &r)
		delete(f.containers, r.Name)
		result = helper.DockerOutput{OK: true}
	case "compose.services":
		result = helper.DockerOutput{OK: true, Output: "web\nworker\n"}
	case "compose.up":
		var r helper.ComposeRequest
		_ = json.Unmarshal(raw, &r)
		f.projects[r.Project] = true
		result = helper.DockerOutput{OK: true}
	case "compose.ps":
		var r helper.ComposeRequest
		_ = json.Unmarshal(raw, &r)
		switch {
		case !f.projects[r.Project]:
			result = helper.DockerOutput{OK: true, Output: ""}
		case f.broken[r.Project]:
			result = helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"exited","ExitCode":2}` + "\n"}
		default:
			result = helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"running","Health":"healthy"}` + "\n" +
				`{"Service":"worker","State":"running"}` + "\n" + `{"Service":"migrate","State":"exited","ExitCode":0}` + "\n"}
		}
	case "systemd.install_unit":
		var r helper.InstallUnitRequest
		_ = json.Unmarshal(raw, &r)
		f.units[r.Name] = r.Content
	case "systemd.control":
		var r helper.SystemdControlRequest
		_ = json.Unmarshal(raw, &r)
		_, ok := f.units[r.Unit]
		result = helper.SystemdControlReply{Active: ok && !f.broken[r.Unit]}
	case "workload.inspect":
		var r helper.WorkloadRequest
		_ = json.Unmarshal(raw, &r)
		image, ok := f.containers[r.Name]
		result = helper.WorkloadState{Managed: ok, Containers: []helper.WorkloadContainer{{Name: r.Name, Running: ok && !f.broken[image]}}}
	case "workload.control":
	default:
		return fmt.Errorf("unexpected helper op %s", op)
	}
	if out != nil && result != nil {
		b, _ := json.Marshal(result)
		return json.Unmarshal(b, out)
	}
	return nil
}

// orgKey signs desired items like the control plane.
var orgKey = ed25519.NewKeyFromSeed([]byte(strings.Repeat("k", 32)))

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func signingKeys() fakecp.Reply {
	return fakecp.OK([]map[string]any{{
		"key_id": "org-1", "algorithm": "ed25519", "public_key": b64(orgKey.Public().(ed25519.PublicKey)),
		"endorsed_by": nil, "endorsement": nil, "created_at": "2026-01-01T00:00:00Z", "retired_at": nil,
	}})
}

// signed returns the item with key_id and signature, decoded like the API does.
func signed(t *testing.T, it map[string]any) map[string]any {
	t.Helper()
	data, _ := json.Marshal(it)
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		t.Fatal(err)
	}
	msg, err := signing.Canonical(signing.DesiredPayload(agenttest.DeviceID, m))
	if err != nil {
		t.Fatal(err)
	}
	it["key_id"] = "org-1"
	it["signature"] = b64(ed25519.Sign(orgKey, append([]byte("meteorcloud-desired-v1\n"), msg...)))
	return it
}

func sha(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

// env is one test's device: deps against a fake control plane, a fake helper,
// and a module ready to reconcile.
type env struct {
	t    *testing.T
	cp   *fakecp.Server
	d    agent.Deps
	host *fakeHost
	m    *Module
}

func newEnv(t *testing.T) *env {
	t.Helper()
	cp := fakecp.New(t)
	d := agenttest.Deps(t, cp, nil, nil)
	st, err := state.NewStore(d.Paths.StateDir)
	if err != nil {
		t.Fatal(err)
	}
	d.State = st
	cp.On("POST", "/api/v1/agent/heartbeat", fakecp.OK(map[string]any{"device_id": agenttest.DeviceID, "status": "online",
		"heartbeat_interval_seconds": 60}))
	cp.On("GET", "/api/v1/agent/signing-keys", signingKeys())
	host := newFakeHost()
	m := &Module{Helper: host, clock: &fakeClock{now: time.Unix(1000, 0)}, probe: func(context.Context, string) string { return "" }}
	m.setup(d)
	return &env{t: t, cp: cp, d: d, host: host, m: m}
}

// desire scripts GET /software with the items, signed.
func (e *env) desire(items ...map[string]any) {
	list := make([]map[string]any, 0, len(items))
	for _, it := range items {
		list = append(list, signed(e.t, it))
	}
	e.cp.On("GET", "/api/v1/agent/software", fakecp.OK(list))
}

// artifact serves bytes for an artifact id with its checksum.
func (e *env) artifact(id string, data []byte) {
	e.cp.On("GET", "/api/v1/agent/artifacts/"+id+"/download",
		fakecp.Reply{Status: 200, Raw: data, Header: map[string]string{"X-Checksum-SHA256": sha(data)}})
}

// cycle reconciles once and returns the reports of that cycle only, per
// workload: what it sent to the server (outcomes go at once), overlaid by what
// it left in the outbox. Reports of earlier cycles are cleared first.
func (e *env) cycle() map[string]report {
	e.t.Helper()
	var ids []string
	pending, err := e.d.Outbox.Pending(state.KindSoftwareReport)
	if err != nil {
		e.t.Fatal(err)
	}
	for _, r := range pending {
		ids = append(ids, r.ID)
	}
	if err := e.d.Outbox.Ack(ids...); err != nil {
		e.t.Fatal(err)
	}
	sent := len(e.cp.Calls("POST", "/api/v1/agent/heartbeat"))
	e.m.reconcile(context.Background())

	out := map[string]report{}
	for _, c := range e.cp.Calls("POST", "/api/v1/agent/heartbeat")[sent:] {
		list, _ := c.Body["software_report"].([]any)
		for _, r := range list {
			if m, ok := r.(map[string]any); ok && m["sync_status"] != statusUpdating {
				out[m["workload_key"].(string)] = report(m)
			}
		}
	}
	for k, r := range e.pending() {
		out[k] = r
	}
	return out
}

// pending returns the reports waiting in the outbox.
func (e *env) pending() map[string]report {
	e.t.Helper()
	pending, err := e.d.Outbox.Pending(state.KindSoftwareReport)
	if err != nil {
		e.t.Fatal(err)
	}
	out := map[string]report{}
	for _, r := range pending {
		var rep report
		if err := json.Unmarshal(r.Body, &rep); err != nil {
			e.t.Fatal(err)
		}
		out[r.Key] = rep
	}
	return out
}

// progress returns the progress percentages sent in progress-only heartbeats.
func (e *env) progress() []int {
	var out []int
	for _, c := range e.cp.Calls("POST", "/api/v1/agent/heartbeat") {
		list, _ := c.Body["software_report"].([]any)
		for _, r := range list {
			if m, ok := r.(map[string]any); ok && m["sync_status"] == statusUpdating {
				out = append(out, int(m["progress_percent"].(float64)))
			}
		}
	}
	return out
}

// fakeClock advances only when the gate sleeps.
type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Sleep(ctx context.Context, d time.Duration) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
	return ctx.Err() == nil
}

var _ runtime.Caller = (*fakeHost)(nil)
