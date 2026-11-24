// Package systemd is the deploy runtime for kind "systemd": the release's
// artifact is a unit file, installed, enabled and (unless spec.start is false)
// restarted by the root helper.
package systemd

import (
	"context"
	"fmt"
	"os"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

// Runtime installs systemd units through the helper.
type Runtime struct {
	Helper runtime.Caller
}

// Kind implements runtime.Runtime.
func (Runtime) Kind() string { return "systemd" }

// unit returns spec.unit_name, or the workload key.
func unit(w runtime.Workload) (string, error) {
	return runtime.CheckName("unit_name", w.Spec.Text("unit_name", w.Key))
}

// Apply implements runtime.Runtime.
func (r Runtime) Apply(ctx context.Context, w runtime.Workload) (runtime.Result, error) {
	name, err := unit(w)
	if err != nil {
		return runtime.Result{}, err
	}
	content, err := os.ReadFile(w.Artifact)
	if err != nil {
		return runtime.Result{}, runtime.Failf("Unit file not found: %s", w.Artifact)
	}
	start := w.Spec.Bool("start", true)
	req := helper.InstallUnitRequest{Name: name, Content: string(content), NoStart: !start}
	if err := r.Helper.Call(ctx, "systemd.install_unit", req, nil); err != nil {
		return runtime.Result{}, err
	}
	if start && !r.active(ctx, name) {
		return runtime.Result{}, runtime.Failf("Unit %s is not active after apply.", name)
	}
	return runtime.Result{Health: runtime.Health{Status: runtime.Healthy}}, nil
}

// Check implements runtime.Runtime: active, or not started on purpose.
func (r Runtime) Check(ctx context.Context, w runtime.Workload) runtime.Health {
	name, err := unit(w)
	if err != nil {
		return runtime.Health{Status: runtime.Unhealthy, Problem: err.Error()}
	}
	if !w.Spec.Bool("start", true) {
		return runtime.Health{Status: runtime.Healthy}
	}
	if !r.active(ctx, name) {
		return runtime.Health{Status: runtime.Unhealthy, Problem: fmt.Sprintf("systemd unit %s is not active", name)}
	}
	return runtime.Health{Status: runtime.Healthy}
}

// Remove implements runtime.Runtime: stop, disable and delete the unit.
func (r Runtime) Remove(ctx context.Context, w runtime.Workload) error {
	name, err := unit(w)
	if err != nil {
		return err
	}
	return r.Helper.Call(ctx, "systemd.control", helper.SystemdControlRequest{Unit: name, Action: "remove"}, nil)
}

func (r Runtime) active(ctx context.Context, name string) bool {
	var out helper.SystemdControlReply
	err := r.Helper.Call(ctx, "systemd.control", helper.SystemdControlRequest{Unit: name, Action: "is_active"}, &out)
	return err == nil && out.Active
}
