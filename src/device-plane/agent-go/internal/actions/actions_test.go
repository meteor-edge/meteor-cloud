package actions

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/fakecp"
)

const (
	listRoute   = "/api/v1/agent/actions"
	reportRoute = "/api/v1/agent/actions/report"
)

// fakeHelper records helper calls; ops in fail return an error.
type fakeHelper struct {
	mu    sync.Mutex
	ops   []string
	parms []string
	fail  map[string]error
	// onCall runs before answering (to look at what was reported meanwhile).
	onCall func(op string)
}

func (f *fakeHelper) Call(_ context.Context, op string, params, _ any) error {
	if f.onCall != nil {
		f.onCall(op)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	b, _ := json.Marshal(params)
	f.ops = append(f.ops, op)
	f.parms = append(f.parms, string(b))
	return f.fail[op]
}

func results(t *testing.T, c fakecp.Call) []api.ActionResult {
	t.Helper()
	var out []api.ActionResult
	b, _ := json.Marshal(c.Body["results"])
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestActionsRunInOrderAndReport(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{
		{"id": "a1", "kind": "reboot"},
		{"id": "a2", "kind": "set_password", "username": "pi", "password": "s3cret-pass"},
		{"id": "a3", "kind": "add_user", "username": "ops", "password": "n3w-pass!", "sudo": true, "authorized_key": "ssh-ed25519 AAAA ops"},
		{"id": "a4", "kind": "frobnicate"},
		{"id": "a5", "kind": "update_agent", "from_version": "0.1.0"},
	}}))
	cp.On("POST", reportRoute, fakecp.OK(map[string]any{"accepted": 1}), fakecp.OK(map[string]any{"accepted": 4}))
	d := agenttest.Deps(t, cp, nil, nil)
	h := &fakeHelper{}
	reportedBeforeReboot := -1
	h.onCall = func(op string) {
		if op == "power.reboot" {
			reportedBeforeReboot = len(cp.Calls("POST", reportRoute))
		}
	}
	r := &runner{d: d, helper: h, backoff: api.NewBackoff()}

	r.collect(context.Background())
	if wait := r.flush(context.Background()); wait != 0 {
		t.Fatalf("flush left results (retry in %v)", wait)
	}

	// In the server's order (the contract suite records it), and the reboot's
	// done report goes out on its own before the reboot starts.
	if !slices.Equal(h.ops, []string{"power.reboot", "users.set_password", "users.add", "update.start"}) {
		t.Errorf("helper ops = %v", h.ops)
	}
	if reportedBeforeReboot != 1 {
		t.Errorf("reports sent before the reboot = %d, want 1", reportedBeforeReboot)
	}
	if h.parms[2] != `{"username":"ops","password":"n3w-pass!","sudo":true,"authorized_key":"ssh-ed25519 AAAA ops"}` {
		t.Errorf("users.add params = %s", h.parms[2])
	}
	reports := cp.Calls("POST", reportRoute)
	if len(reports) != 2 {
		t.Fatalf("reports = %d, want 2", len(reports))
	}
	if first := results(t, reports[0]); len(first) != 1 || first[0].ID != "a1" || first[0].State != "done" {
		t.Errorf("first report = %+v, want only the reboot", first)
	}
	var all []api.ActionResult
	for _, c := range reports {
		all = append(all, results(t, c)...)
	}
	// update_agent is reported later, from the updater's outcome.
	if len(all) != 4 {
		t.Fatalf("results = %+v", all)
	}
	byID := map[string]api.ActionResult{}
	for _, res := range all {
		byID[res.ID] = res
	}
	if byID["a1"].State != "done" || byID["a2"].State != "done" || byID["a3"].State != "done" {
		t.Errorf("done results = %+v", byID)
	}
	if byID["a4"].State != "failed" || !strings.HasPrefix(strings.ToLower(byID["a4"].Error), "unsupported") {
		t.Errorf("unknown kind = %+v", byID["a4"])
	}
	if _, ok := byID["a5"]; ok {
		t.Errorf("update_agent reported before the update finished: %+v", byID["a5"])
	}
	if reports[0].Header.Get("Authorization") != "Bearer "+agenttest.Token {
		t.Errorf("report auth = %q", reports[0].Header.Get("Authorization"))
	}
}

func TestFailedActionIsReportedWithRedactedError(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{
		{"id": "a1", "kind": "set_password", "username": "ghost", "password": "s3cret-pass"},
		{"id": "a2", "kind": "shutdown"},
	}}))
	cp.On("POST", reportRoute, fakecp.OK(map[string]any{"accepted": 1}))
	d := agenttest.Deps(t, cp, nil, nil)
	long := "password=s3cret-pass " + strings.Repeat("x", 600)
	h := &fakeHelper{fail: map[string]error{"users.set_password": errors.New(long), "power.shutdown": errors.New("systemctl failed")}}
	r := &runner{d: d, helper: h, backoff: api.NewBackoff()}
	r.collect(context.Background())
	r.flush(context.Background())

	var all []api.ActionResult
	for _, c := range cp.Calls("POST", reportRoute) {
		all = append(all, results(t, c)...)
	}
	// set_password failed; then shutdown said done first, then failed.
	if len(all) != 3 {
		t.Fatalf("results = %+v", all)
	}
	byID := map[string][]api.ActionResult{}
	for _, res := range all {
		byID[res.ID] = append(byID[res.ID], res)
	}
	if len(byID["a1"]) != 1 || byID["a1"][0].State != "failed" {
		t.Fatalf("set_password = %+v", byID["a1"])
	}
	if len(byID["a2"]) != 2 || byID["a2"][0].State != "done" || byID["a2"][1].State != "failed" {
		t.Fatalf("shutdown = %+v", byID["a2"])
	}
	if strings.Contains(byID["a1"][0].Error, "s3cret") || len(byID["a1"][0].Error) > maxError {
		t.Errorf("error not redacted or not cut: %q", byID["a1"][0].Error)
	}
}

func TestPowerActionSkippedWhenOutboxPutFails(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{
		{"id": "a1", "kind": "reboot"},
	}}))
	d := agenttest.Deps(t, cp, nil, nil)
	outbox := filepath.Join(d.Paths.StateDir, "outbox")
	if err := os.Chmod(outbox, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(outbox, 0o700) })
	h := &fakeHelper{}
	r := &runner{d: d, helper: h, backoff: api.NewBackoff()}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	r.collect(ctx)
	if len(h.ops) != 0 {
		t.Errorf("reboot must not run when the done report cannot be kept; ops = %v", h.ops)
	}
	if len(r.held) != 1 || r.held[0].ID != "a1" {
		t.Errorf("held = %+v", r.held)
	}
}

func TestResultsSurviveAnOutage(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", reportRoute, fakecp.Fail(503, "unavailable"), fakecp.OK(map[string]any{"accepted": 1}))
	d := agenttest.Deps(t, cp, nil, nil)
	r := &runner{d: d, helper: &fakeHelper{}, backoff: api.NewBackoff()}
	r.put(context.Background(), api.Action{ID: "a1", Kind: "reboot"}, nil)
	if wait := r.flush(context.Background()); wait <= 0 {
		t.Fatal("a failed report must ask for a retry")
	}
	if p, _ := d.Outbox.Pending(KindReport); len(p) != 1 {
		t.Fatalf("pending after failure = %d", len(p))
	}
	if wait := r.flush(context.Background()); wait != 0 {
		t.Fatalf("retry = %v", wait)
	}
	if p, _ := d.Outbox.Pending(KindReport); len(p) != 0 {
		t.Errorf("pending after success = %d", len(p))
	}
}

func TestBatchesOf50(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", reportRoute, fakecp.OK(map[string]any{"accepted": 50}))
	d := agenttest.Deps(t, cp, nil, nil)
	r := &runner{d: d, helper: &fakeHelper{}, backoff: api.NewBackoff()}
	for i := 0; i < 60; i++ {
		r.put(context.Background(), api.Action{ID: "a"}, nil)
	}
	r.flush(context.Background())
	calls := cp.Calls("POST", reportRoute)
	if len(calls) != 2 || len(results(t, calls[0])) != 50 || len(results(t, calls[1])) != 10 {
		t.Errorf("batches = %d", len(calls))
	}
}

func TestRefusedTokenGoesToIdentity(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.Fail(401, "invalid_token"))
	d := agenttest.Deps(t, cp, nil, nil)
	r := &runner{d: d, helper: &fakeHelper{}, backoff: api.NewBackoff()}
	r.collect(context.Background())
	if _, err := d.Identity.Token(); err == nil {
		t.Error("a 401 must make identity stop handing out the token")
	}
}

func TestModuleCollectsOnSignal(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{{"id": "a1", "kind": "frobnicate"}}}))
	cp.On("POST", reportRoute, fakecp.OK(map[string]any{"accepted": 1}))
	d := agenttest.Deps(t, cp, nil, nil)
	agenttest.Start(t, &Module{}, d)
	// A signal sent before Run subscribed is not delivered: send until seen.
	deadline := time.Now().Add(5 * time.Second)
	for len(cp.Calls("POST", reportRoute)) == 0 {
		if time.Now().After(deadline) {
			t.Fatal("no report after the WorkWaiting signal")
		}
		d.Signals.Publish(agent.WorkWaiting{Kind: "actions"})
		time.Sleep(20 * time.Millisecond)
	}
}

func TestKinds(t *testing.T) {
	if got := Kinds(); !slices.Equal(got, []string{"reboot", "shutdown", "set_password", "add_user", "update_agent"}) {
		t.Errorf("Kinds = %v", got)
	}
}

func TestPyRepr(t *testing.T) {
	for in, want := range map[string]string{"frobnicate": "'frobnicate'", "it's": `"it's"`, "a\nb": `"a\nb"`} {
		if got := pyRepr(in); got != want {
			t.Errorf("pyRepr(%q) = %s, want %s", in, got, want)
		}
	}
}
