package actions

import (
	"context"
	"encoding/json"
	"errors"
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

	if !slices.Equal(h.ops, []string{"power.reboot", "users.set_password", "users.add"}) {
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
	first, second := results(t, reports[0]), results(t, reports[1])
	if len(first) != 1 || first[0] != (api.ActionResult{ID: "a1", State: "done"}) {
		t.Errorf("first report = %+v", first)
	}
	if len(second) != 4 || second[0].State != "done" || second[1].State != "done" {
		t.Fatalf("second report = %+v", second)
	}
	if second[2].State != "failed" || !strings.HasPrefix(strings.ToLower(second[2].Error), "unsupported") {
		t.Errorf("unknown kind = %+v", second[2])
	}
	if second[3].State != "failed" || !strings.HasPrefix(strings.ToLower(second[3].Error), "unsupported") {
		t.Errorf("update_agent stub = %+v", second[3])
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
	// set_password failed; shutdown said done first, then failed.
	if len(all) != 3 || all[0].ID != "a1" || all[0].State != "failed" || all[1] != (api.ActionResult{ID: "a2", State: "done"}) || all[2].State != "failed" {
		t.Fatalf("results = %+v", all)
	}
	if strings.Contains(all[0].Error, "s3cret") || len(all[0].Error) > maxError {
		t.Errorf("error not redacted or not cut: %q", all[0].Error)
	}
}

func TestResultsSurviveAnOutage(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", reportRoute, fakecp.Fail(503, "unavailable"), fakecp.OK(map[string]any{"accepted": 1}))
	d := agenttest.Deps(t, cp, nil, nil)
	r := &runner{d: d, helper: &fakeHelper{}, backoff: api.NewBackoff()}
	r.put(api.Action{ID: "a1", Kind: "reboot"}, nil)
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
		r.put(api.Action{ID: "a"}, nil)
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
	if got := Kinds(); !slices.Equal(got, []string{"reboot", "shutdown", "set_password", "add_user"}) {
		t.Errorf("Kinds = %v (update_agent is a stub and not advertised)", got)
	}
}

func TestPyRepr(t *testing.T) {
	for in, want := range map[string]string{"frobnicate": "'frobnicate'", "it's": `"it's"`, "a\nb": `"a\nb"`} {
		if got := pyRepr(in); got != want {
			t.Errorf("pyRepr(%q) = %s, want %s", in, got, want)
		}
	}
}
