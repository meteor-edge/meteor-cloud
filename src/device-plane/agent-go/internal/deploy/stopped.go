package deploy

import (
	"context"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
	wl "meteorcloud/meteorcli/internal/workload"
)

// stoppedByUser reports a workload the user stopped from the console, while
// the desired release is the one that is installed (or was rolled back from):
// it is neither health-checked nor applied again, and is stopped again when
// something else started it. nil for any other workload; a new release or
// revision goes through install, which clears the record. locked says that the
// caller holds the workload lock.
func (c *cycle) stoppedByUser(ctx context.Context, it item, rec workload, had, locked bool) report {
	if !had || rec.Item == nil {
		return nil
	}
	stop := c.r.stops.Get(it.key())
	if !stop.Any() {
		return nil
	}
	release, revision, ok := it.identity()
	back := rec.RolledBackFrom
	var rep report
	switch {
	case ok && back != nil && back.ReleaseID == release && back.Revision == revision:
		reason := back.Reason
		if reason == "" {
			reason = "health check failed"
		}
		rep = rolledBack(it, rec.Item, runtime.Unknown, reason)
	case c.alreadyApplied(it, rec):
		rep = inSync(it, runtime.Unknown, "")
	default:
		return nil
	}
	c.stopAgain(ctx, installedFrom(rec.Item), locked)
	// A service may be stopped while the others run; health stays unknown.
	rep["stopped_by_user"] = true
	return rep
}

// stopAgain stops what the record says is stopped but runs (Docker's restart
// policy "always" after a daemon restart, a token refresh, a manual start).
//
// It needs the workload lock, and reads the record under it: a Start that runs
// meanwhile must not be undone. When an action holds the lock, the next cycle
// looks again.
func (c *cycle) stopAgain(ctx context.Context, in Installed, locked bool) {
	d := c.r.d
	if !locked {
		release, ok := d.Workloads.TryAcquire()
		if !ok {
			return
		}
		defer release()
	}
	stop := c.r.stops.Get(in.Key)
	if !stop.Any() {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, checkTimeout)
	defer cancel()
	req := helper.WorkloadRequest{Kind: in.Kind, Key: in.Key, Name: in.Name}
	var st helper.WorkloadState
	if err := c.r.helper.Call(ctx, "workload.inspect", req, &st); err != nil || !st.Managed {
		return
	}
	for _, service := range StillRunning(st, stop) {
		d.Log.Info("stopping a workload the user stopped", "workload", in.Key, "service", service)
		r := helper.WorkloadControlRequest{WorkloadRequest: req, Verb: "stop", Service: service}
		if err := c.r.helper.Call(ctx, "workload.control", r, nil); err != nil {
			d.Log.Warn("could not stop a workload the user stopped", "workload", in.Key, "err", err)
		}
	}
}

// StillRunning returns the services ("" for the whole workload) that stop says
// are stopped but that run.
func StillRunning(st helper.WorkloadState, stop wl.Stop) []string {
	if stop.All && st.Unit != nil && (st.Unit.ActiveState == "active" || st.Unit.ActiveState == "activating") {
		return []string{""}
	}
	running := map[string]bool{}
	for _, ct := range st.Containers {
		if ct.Running {
			running[ct.Service] = true
		}
	}
	if stop.All {
		if len(running) > 0 {
			return []string{""}
		}
		return nil
	}
	var out []string
	for _, s := range stop.Services {
		if running[s] {
			out = append(out, s)
		}
	}
	return out
}

// clearStop forgets that the user stopped key: a new deploy started it.
func (c *cycle) clearStop(key string) {
	if err := c.r.stops.Set(key, wl.Stop{}); err != nil {
		c.r.d.Log.Error("clear the stopped state of a workload", "workload", key, "err", err)
	}
}
