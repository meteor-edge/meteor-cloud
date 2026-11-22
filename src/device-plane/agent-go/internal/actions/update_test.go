package actions

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/update"
)

func updateRunner(t *testing.T, h *fakeHelper) (*runner, *fakecp.Server) {
	t.Helper()
	cp := fakecp.New(t)
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{
		{"id": "act-1", "kind": "update_agent", "from_version": "1.2.3"},
	}}))
	cp.On("POST", reportRoute, fakecp.OK(map[string]any{"accepted": 1}))
	d := agenttest.Deps(t, cp, nil, nil)
	return &runner{d: d, helper: h, backoff: api.NewBackoff()}, cp
}

func reported(t *testing.T, cp *fakecp.Server) []api.ActionResult {
	t.Helper()
	var all []api.ActionResult
	for _, c := range cp.Calls("POST", reportRoute) {
		all = append(all, results(t, c)...)
	}
	return all
}

func TestUpdateAgentIsReportedFromTheOutcome(t *testing.T) {
	cases := map[string]struct {
		outcome update.Outcome
		state   string
		err     string
	}{
		"updated":         {update.Outcome{Status: update.StatusUpdated, FromVersion: "1.2.3", ToVersion: "1.3.0"}, "done", ""},
		"already current": {update.Outcome{Status: update.StatusAlreadyCurrent}, "done", ""},
		"rolled back": {update.Outcome{Status: update.StatusRolledBack,
			Error: "meteorcli 1.3.0 did not start: exit 1; rolled back to 1.2.3"}, "failed", "rolled back to 1.2.3"},
		"refused":   {update.Outcome{Status: update.StatusFailed, Error: "release is not signed by a MeteorCloud release key"}, "failed", "not signed"},
		"no reason": {update.Outcome{Status: "weird"}, "failed", `ended with "weird"`},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			h := &fakeHelper{}
			r, cp := updateRunner(t, h)
			ctx := context.Background()
			r.collect(ctx)
			r.flush(ctx)
			if !slices.Equal(h.ops, []string{"update.start"}) || h.parms[0] != `{"action_id":"act-1"}` {
				t.Fatalf("helper = %v %v", h.ops, h.parms)
			}
			if got := reported(t, cp); len(got) != 0 {
				t.Fatalf("reported before the outcome: %+v", got)
			}
			// The updater is still running: nothing to report.
			if !r.settle(ctx) {
				t.Fatal("settle says nothing runs while the pending file exists")
			}
			// An update started from the command line (or an older action)
			// does not settle this one.
			must(t, r.d.State.Write(update.OutcomeFile, update.FileVersion, update.Outcome{Status: update.StatusUpdated}))
			if !r.settle(ctx) {
				t.Fatal("an outcome for another action settled this one")
			}
			o := c.outcome
			o.ActionID = "act-1"
			must(t, r.d.State.Write(update.OutcomeFile, update.FileVersion, o))
			if r.settle(ctx) {
				t.Fatal("still running after the outcome")
			}
			r.flush(ctx)
			got := reported(t, cp)
			if len(got) != 1 || got[0].ID != "act-1" || got[0].State != c.state || !strings.Contains(got[0].Error, c.err) {
				t.Fatalf("report = %+v", got)
			}
			var p update.Pending
			if _, err := r.d.State.Read(update.PendingFile, &p); err == nil {
				t.Fatal("pending file kept after the report")
			}
			if r.settle(ctx) {
				t.Fatal("settled twice")
			}
		})
	}
}

func TestUpdateAgentTimesOut(t *testing.T) {
	r, cp := updateRunner(t, &fakeHelper{})
	ctx := context.Background()
	r.collect(ctx)
	r.clock = func() time.Time { return time.Now().Add(updateTimeout + time.Minute) }
	if r.settle(ctx) {
		t.Fatal("still running after the timeout")
	}
	r.flush(ctx)
	if got := reported(t, cp); len(got) != 1 || got[0].State != "failed" || !strings.Contains(got[0].Error, "did not finish in time") {
		t.Fatalf("report = %+v", got)
	}
}

func TestUpdateAgentHelperFailure(t *testing.T) {
	h := &fakeHelper{fail: map[string]error{"update.start": errors.New("Unit meteorcli-update.service already exists")}}
	r, cp := updateRunner(t, h)
	ctx := context.Background()
	r.collect(ctx)
	r.flush(ctx)
	got := reported(t, cp)
	if len(got) != 1 || got[0].State != "failed" || !strings.Contains(got[0].Error, "could not start the meteorcli updater") {
		t.Fatalf("report = %+v", got)
	}
	if r.settle(ctx) {
		t.Fatal("a failed start left a pending update")
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func TestSecondUpdateKeepsTheFirstRecord(t *testing.T) {
	r, cp := updateRunner(t, &fakeHelper{})
	ctx := context.Background()
	r.collect(ctx) // act-1 starts
	cp.On("GET", listRoute, fakecp.OK(map[string]any{"actions": []map[string]any{
		{"id": "act-2", "kind": "update_agent"},
	}}))
	h := &fakeHelper{}
	r.helper = h
	r.collect(ctx)
	r.flush(ctx)
	if len(h.ops) != 0 {
		t.Fatalf("a second updater was started: %v", h.ops)
	}
	got := reported(t, cp)
	if len(got) != 1 || got[0].ID != "act-2" || got[0].State != "failed" || !strings.Contains(got[0].Error, "another meteorcli update is running") {
		t.Fatalf("report = %+v", got)
	}
	var p update.Pending
	if _, err := r.d.State.Read(update.PendingFile, &p); err != nil || p.ActionID != "act-1" {
		t.Fatalf("first record = %+v, %v", p, err)
	}
}

func TestSettleKeepsPollingAfterAReadError(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads any file")
	}
	r, _ := updateRunner(t, &fakeHelper{})
	ctx := context.Background()
	r.collect(ctx)
	path := filepath.Join(r.d.State.Dir(), update.PendingFile)
	must(t, os.Chmod(path, 0))
	t.Cleanup(func() { _ = os.Chmod(path, 0o600) })
	if !r.settle(ctx) {
		t.Fatal("settle stopped polling after a read error")
	}
}
