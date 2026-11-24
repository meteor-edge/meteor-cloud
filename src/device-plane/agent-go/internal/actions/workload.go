package actions

import (
	"context"
	"fmt"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/deploy"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/workload"
	"meteorcloud/meteorcli/internal/workloads"
)

// installed finds the workload of a. Only what the deploy module installed can
// be named; the helper checks again what belongs to it.
func installed(e env, a api.Action) (deploy.Installed, error) {
	key := str(a.WorkloadKey)
	all, err := deploy.InstalledWorkloads(e.d.State)
	if err != nil {
		return deploy.Installed{}, err
	}
	in, ok := all[key]
	if !ok {
		return deploy.Installed{}, fmt.Errorf("this device has no workload %q", key)
	}
	return in, nil
}

func snapshotWorkload(ctx context.Context, e env, a api.Action) error {
	in, err := installed(e, a)
	if err != nil {
		return err
	}
	return workloads.RefreshAll(ctx, e.d, e.helper, in)
}

// controlWorkload runs verb on a workload or one compose service of it. The
// stop record is written before a stop and removed before a start, so a crash
// in between leaves what the user asked for.
func controlWorkload(verb string) func(context.Context, env, api.Action) error {
	return func(ctx context.Context, e env, a api.Action) error {
		in, err := installed(e, a)
		if err != nil {
			return err
		}
		service := str(a.Service)
		if service != "" && in.Kind != "docker_compose" {
			return fmt.Errorf("workload %q has no services", in.Key)
		}
		release, err := e.d.Workloads.AcquireSoon(ctx)
		if err != nil {
			return err
		}
		defer release()
		stops := workload.Stops{Store: e.d.State}
		before := stops.Get(in.Key)
		next, do := before, verb
		switch {
		case verb == "stop":
			next = before.With(service)
		case before.All && service != "":
			return fmt.Errorf("workload %q is stopped; start all of it first", in.Key)
		default:
			next = before.Without(service)
			// Restarting what is stopped starts it again.
			if verb == "restart" && before.All {
				do = "start"
			}
		}
		if err := stops.Set(in.Key, next); err != nil {
			return err
		}
		req := helper.WorkloadControlRequest{Verb: do, Service: service,
			WorkloadRequest: helper.WorkloadRequest{Kind: in.Kind, Key: in.Key, Name: in.Name}}
		if err := e.helper.Call(ctx, "workload.control", req, nil); err != nil {
			if verb == "stop" {
				if serr := stops.Set(in.Key, before); serr != nil {
					e.d.Log.Error("restore the stopped state of a workload", "workload", in.Key, "err", serr)
				}
			}
			return err
		}
		// The console shows the result at once: the new snapshot, then the
		// deploy module's health report.
		if err := workloads.Refresh(ctx, e.d, e.helper, in); err != nil {
			e.d.Log.Debug("refresh a workload snapshot", "workload", in.Key, "err", err)
		}
		e.d.Signals.Publish(agent.WorkWaiting{Kind: "software"})
		return nil
	}
}
