package systemd

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

type fake struct {
	active bool
	calls  []string
	last   map[string]json.RawMessage
}

func (f *fake) Call(_ context.Context, op string, params, out any) error {
	b, _ := json.Marshal(params)
	if f.last == nil {
		f.last = map[string]json.RawMessage{}
	}
	f.calls = append(f.calls, op)
	f.last[op] = b
	if r, ok := out.(*helper.SystemdControlReply); ok {
		r.Active = f.active
	}
	return nil
}

func TestApplyAndCheck(t *testing.T) {
	unit := filepath.Join(t.TempDir(), "u")
	_ = os.WriteFile(unit, []byte("[Service]\nExecStart=/bin/true\n"), 0o600)
	f := &fake{active: true}
	r := Runtime{Helper: f}
	w := runtime.Workload{Key: "demo.service", Spec: runtime.Spec{}, Artifact: unit}
	if res, err := r.Apply(context.Background(), w); err != nil || res.Health.Status != runtime.Healthy {
		t.Fatalf("Apply = %+v, %v", res, err)
	}
	var req helper.InstallUnitRequest
	_ = json.Unmarshal(f.last["systemd.install_unit"], &req)
	if req.Name != "demo.service" || req.NoStart || req.Content == "" {
		t.Fatalf("install %+v", req)
	}
	f.active = false
	if _, err := r.Apply(context.Background(), w); err == nil || err.Error() != "Unit demo.service is not active after apply." {
		t.Fatalf("inactive after apply: %v", err)
	}
	if h := r.Check(context.Background(), w); h.Status != runtime.Unhealthy || h.Problem != "systemd unit demo.service is not active" {
		t.Fatalf("Check %+v", h)
	}
	// start: false installs without starting and counts as healthy.
	w.Spec = runtime.Spec{"start": false}
	if res, err := r.Apply(context.Background(), w); err != nil || res.Health.Status != runtime.Healthy {
		t.Fatalf("no start: %+v, %v", res, err)
	}
	if h := r.Check(context.Background(), w); h.Status != runtime.Healthy {
		t.Fatalf("no start Check %+v", h)
	}
	if err := r.Remove(context.Background(), w); err != nil || string(f.last["systemd.control"]) != `{"unit":"demo.service","action":"remove"}` {
		t.Fatalf("Remove: %v %s", err, f.last["systemd.control"])
	}
	if _, err := r.Apply(context.Background(), runtime.Workload{Key: "x", Spec: runtime.Spec{}, Artifact: "/missing"}); err == nil {
		t.Fatal("missing unit file accepted")
	}
}
