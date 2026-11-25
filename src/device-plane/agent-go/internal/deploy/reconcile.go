package deploy

import (
	"context"
	"errors"
	"fmt"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/signing"
)

// errAuth stops a cycle: the server refused the device token (identity knows).
var errAuth = errors.New("device token refused")

// cycle is one reconcile: list the desired software, verify it, install what
// changed, and return a software report for every item. It is the Python
// agent's reconcile_software, step for step.
type cycle struct {
	r        *Module
	token    string
	applied  map[string]workload
	tools    map[string]bool
	verifier *signing.Verifier
	creds    creds
	refused  bool // the token was refused during this cycle
}

// run returns the reports of this cycle, and the reports that came from an
// install attempt (for events).
func (c *cycle) run(ctx context.Context) (reports []report, attempts map[string]bool) {
	d := c.r.d
	raw, err := d.API.Software(ctx, c.token)
	switch {
	case api.IsAuth(err):
		c.authFailed()
		return nil, nil
	case err != nil:
		d.Log.Warn("could not list desired software", "err", err)
		return nil, nil
	}
	attempts = map[string]bool{}

	var accepted []item
	// keep is the workloads whose stored credentials stay: the bound accepted
	// items, and every refused one (a refused item cannot unbind a workload).
	keep := map[string]bool{}
	refuse := func(key, why string) {
		d.Log.Warn("refusing desired software", "workload", key, "reason", why)
		reports = append(reports, failed(key, why))
		attempts[key] = true
		keep[key] = true
	}
	for _, m := range raw {
		it := item(m)
		key := it.key()
		if key == "" {
			continue
		}
		if _, ok := c.r.runtimes[it.kind()]; !ok {
			reports = append(reports, failed(key, "Unsupported release kind: "+pyRepr(it["kind"])))
			attempts[key] = true
			keep[key] = true
			continue
		}
		if c.verifier != nil {
			why, err := c.verifier.Check(ctx, m)
			if err != nil {
				// Only a refused token comes back as an error: no reports, nothing pruned.
				c.authFailed()
				return nil, nil
			}
			if why != "" {
				refuse(key, why)
				continue
			}
		}
		if why := c.stale(it); why != "" {
			refuse(key, why)
			continue
		}
		accepted = append(accepted, it)
	}

	// Prune only after verification, so a refused item cannot drop stored credentials.
	for _, it := range accepted {
		if binding(it) != nil {
			keep[it.key()] = true
		}
	}
	c.creds.prune(keep)

	for _, it := range accepted {
		rep, attempted := c.reconcile(ctx, it)
		if c.refused {
			return nil, nil // reports would be sent with a token the server refused
		}
		if rep != nil {
			reports = append(reports, rep)
			if attempted {
				attempts[it.key()] = true
			}
		}
	}
	return reports, attempts
}

// stale refuses an older revision than the one applied (a replayed signed item).
func (c *cycle) stale(it item) string {
	_, revision, ok := it.identity()
	rec, had := c.applied[it.key()]
	if !ok || !had || revision >= rec.Revision {
		return ""
	}
	return fmt.Sprintf("Desired revision %d is older than applied revision %d; refusing rollback.", revision, rec.Revision)
}

// reconcile handles one accepted item. attempted is false for a report that
// only restates the state (health of an installed workload, a kept rollback).
func (c *cycle) reconcile(ctx context.Context, it item) (rep report, attempted bool) {
	d := c.r.d
	key := it.key()
	rec, had := c.applied[key]

	if rep := c.stoppedByUser(ctx, it, rec, had, false); rep != nil {
		return rep, false
	}
	if rep := c.stillRolledBack(ctx, it, rec, had); rep != nil {
		return rep, false
	}
	if c.refused {
		return nil, false
	}
	wasApplied := had && c.alreadyApplied(it, rec)
	if wasApplied {
		if !rec.Reapply && !c.creds.present(it) {
			rec.Reapply = true
			c.applied[key] = rec
			release, revision, _ := it.identity()
			if err := c.r.applied.save(record{key: key, release: release, revision: revision, reapply: true}); err != nil {
				d.Log.Error("record that a workload needs another apply", "workload", key, "err", err)
			}
		}
		if !rec.Reapply {
			if rep := c.healthOnly(ctx, it); rep != nil {
				// A stop that came in during the check is not a failure.
				if stopped := c.stoppedByUser(ctx, it, rec, had, false); stopped != nil {
					return stopped, false
				}
				return rep, false
			}
		}
	}

	release, err := d.Workloads.Acquire(ctx)
	if err != nil {
		return nil, false // stopping
	}
	defer release()
	// A stop that came in meanwhile wins over an apply of the same release.
	if rep := c.stoppedByUser(ctx, it, rec, had, true); rep != nil {
		return rep, false
	}
	rep, err = c.install(ctx, it, rec, had, wasApplied)
	switch {
	case errors.Is(err, errAuth):
		return nil, false
	case err != nil:
		d.Log.Warn("software reconcile failed", "workload", key, "err", err)
		return failed(key, message(err)), true
	}
	return rep, true
}

// install applies the item, waits for its health gate, and rolls back on failure.
func (c *cycle) install(ctx context.Context, it item, rec workload, had, wasApplied bool) (report, error) {
	d := c.r.d
	key := it.key()
	var app *runtime.App
	if binding(it) != nil {
		if it.kind() == "systemd" {
			return failed(key, "A MeteorCloud application binding needs a docker or docker_compose release."), nil
		}
		var err error
		if app, err = c.creds.forApply(ctx, d.API, c.token, it); err != nil {
			return nil, c.check(err)
		}
		if _, ok := runtime.BoundEnv(it.spec().Env("env"), app)["METEORCLOUD_ENDPOINT"]; !ok {
			return failed(key, "The control plane has no data plane address (DATA_PLANE_PUBLIC_URL). "+
				"Set it, or set METEORCLOUD_ENDPOINT in the release env."), nil
		}
	}
	rep, err := c.apply(ctx, it, app, wasApplied)
	if err != nil {
		return nil, err
	}
	g := gateOf(it.spec())
	if g != nil && rep.status() == statusInSync {
		check := func(ctx context.Context) runtime.Health { return c.check1(ctx, it) }
		if why := g.wait(ctx, check, c.r.probe, c.r.clock); why != "" {
			rep = failed(key, "Health check failed: "+why)
		} else {
			rep = inSync(it, runtime.Healthy, "")
		}
	}
	if g != nil && g.Rollback && rep.status() == statusFailed && had {
		back, err := c.rollBack(ctx, it, rep, rec)
		if err != nil {
			return nil, err
		}
		if back.status() == statusRolledBack {
			return back, nil
		}
		rep = back
	}
	if release, revision, ok := it.identity(); ok && rep.status() == statusInSync {
		// Before the release is recorded: when it fails, the next cycle applies
		// again, and an old stop cannot hit the new release.
		if err := c.clearStop(key); err != nil {
			return nil, fmt.Errorf("clear the stopped state: %w", err)
		}
		if err := c.r.applied.save(record{key: key, release: release, revision: revision, item: it}); err != nil {
			d.Log.Error("record the applied release", "workload", key, "err", err)
		}
		c.applied[key] = workload{ReleaseID: release, Revision: revision, Item: it}
	}
	return rep, nil
}

// apply downloads what the item needs and hands it to its runtime.
func (c *cycle) apply(ctx context.Context, it item, app *runtime.App, wasApplied bool) (report, error) {
	key, kind := it.key(), it.kind()
	artifact := ""
	switch {
	case it.artifactID() != "":
		path, err := c.ensureArtifact(ctx, key, it.artifactID(), it.artifactSHA256())
		if err != nil {
			return nil, c.check(err)
		}
		artifact = path
	case kind == "systemd":
		return failed(key, "systemd release is missing an artifact_id."), nil
	case kind == "docker_compose":
		return failed(key, "docker_compose release is missing an artifact_id."), nil
	case it.spec().Text("image", "") == "":
		return failed(key, "docker release needs an image name or a tarball artifact."), nil
	}
	c.progress(ctx, key, 100)
	if c.refused {
		return nil, errAuth
	}
	if !c.tools[kind] {
		return failed(key, c.r.kinds[kind].missing), nil
	}
	w := runtime.Workload{
		Key: key, Kind: kind, Release: runtime.Stringify(it["release_id"]), Spec: it.spec(), Artifact: artifact, App: app,
		// The same release applied again with a binding: its token file was
		// lost, and the containers must be created again to mount the new one.
		ForceRecreate: kind == "docker_compose" && app != nil && wasApplied,
	}
	var pullErr, secretErr error
	if it.spec().Text("registry", "") == "meteorcloud" || len(it.spec().Strings("registry_images")) > 0 {
		w.PullAuth = func(ctx context.Context) (runtime.PullAuth, error) {
			t, err := c.r.d.API.RegistryPullToken(ctx, c.token, key)
			if err != nil {
				pullErr = err
				return runtime.PullAuth{}, runtime.Failf("Could not sign in to the container registry: %s", message(err))
			}
			return runtime.PullAuth{Registry: t.Registry, Username: t.Username, Password: t.Password}, nil
		}
	}
	if kind == "docker" && len(it.spec().Strings("secret_env")) > 0 {
		w.SecretEnv = func(ctx context.Context) (map[string]string, error) {
			env, err := c.r.d.API.ReleaseSecrets(ctx, c.token, key, w.Release)
			if err != nil {
				secretErr = err
				return nil, runtime.Failf("Could not get the app's secrets: %s", message(err))
			}
			return env, nil
		}
	}
	actx, cancel := context.WithTimeout(ctx, applyTimeout)
	defer cancel()
	res, err := c.r.runtimes[kind].Apply(actx, w)
	for _, e := range []error{pullErr, secretErr} {
		if e != nil && api.IsAuth(e) {
			return nil, c.check(e)
		}
	}
	if err != nil {
		return failed(key, message(err)), nil
	}
	if rep := inSyncFrom(it, res.Health); rep != nil {
		return rep, nil
	}
	return inSync(it, runtime.Unknown, res.Health.Problem), nil
}

// check turns a refused token into errAuth, after telling identity.
func (c *cycle) check(err error) error {
	if api.IsAuth(err) {
		c.authFailed()
		return errAuth
	}
	return err
}

func (c *cycle) authFailed() {
	if !c.refused {
		c.refused = true
		c.r.d.Identity.AuthFailed(c.token)
	}
}

// check1 asks the item's runtime for its health, with a timeout.
func (c *cycle) check1(ctx context.Context, it item) runtime.Health {
	ctx, cancel := context.WithTimeout(ctx, checkTimeout)
	defer cancel()
	return c.r.runtimes[it.kind()].Check(ctx, runtime.Workload{Key: it.key(), Kind: it.kind(), Spec: it.spec()})
}

// alreadyApplied reports whether rec is this item's release and revision.
func (c *cycle) alreadyApplied(it item, rec workload) bool {
	release, revision, ok := it.identity()
	return ok && rec.ReleaseID == release && rec.Revision == revision
}

// healthOnly reports an installed workload's health without applying it
// again; a stopped unit or container is in_sync + unhealthy, never
// reinstalled. nil (apply again) when the runtime is missing or the
// workload is gone.
func (c *cycle) healthOnly(ctx context.Context, it item) report {
	if _, ok := c.r.runtimes[it.kind()]; !ok || !c.tools[it.kind()] {
		return nil
	}
	return inSyncFrom(it, c.check1(ctx, it))
}

// stillRolledBack reports a desired release this device already rolled back
// from (until the desired release changes), nil to go on. The release it
// runs instead keeps its application token fresh, and is applied again (not
// the failed one) when its token file was lost.
func (c *cycle) stillRolledBack(ctx context.Context, it item, rec workload, had bool) report {
	if !had || rec.RolledBackFrom == nil || rec.Item == nil {
		return nil
	}
	release, revision, ok := it.identity()
	if !ok || rec.RolledBackFrom.ReleaseID != release || rec.RolledBackFrom.Revision != revision {
		return nil
	}
	reason := rec.RolledBackFrom.Reason
	if reason == "" {
		reason = "health check failed"
	}
	health := runtime.Unknown
	if !c.creds.present(rec.Item) {
		release, err := c.r.d.Workloads.Acquire(ctx)
		if err != nil {
			return nil
		}
		if stopped := c.stoppedByUser(ctx, it, rec, had, true); stopped != nil {
			release()
			return stopped
		}
		restored, err := c.restore(ctx, rec.Item)
		release()
		if err != nil {
			return nil // errAuth: the cycle stops
		}
		if restored.status() != statusInSync {
			return failed(it.key(), fmt.Sprintf("%s; restoring %s failed: %s", reason, rec.Item.version(), restored.lastError()))
		}
		health = restored.health()
	} else if cur := c.healthOnly(ctx, rec.Item); cur != nil {
		health = cur.health()
	}
	return rolledBack(it, rec.Item, health, reason)
}

// restore applies a release this device ran before (a rollback target), with
// its application credentials. It returns errAuth only.
func (c *cycle) restore(ctx context.Context, previous item) (report, error) {
	d := c.r.d
	app, err := c.creds.forApply(ctx, d.API, c.token, previous)
	if err != nil {
		if err := c.check(err); errors.Is(err, errAuth) {
			return nil, err
		}
		return failed(previous.key(), message(err)), nil
	}
	restored, err := c.apply(ctx, previous, app, false)
	if errors.Is(err, errAuth) {
		return nil, err
	}
	if err != nil {
		restored = failed(previous.key(), message(err))
	}
	return restored, nil
}

// rollBack applies the release that ran before it; failure stands when there is none.
func (c *cycle) rollBack(ctx context.Context, it item, failure report, rec workload) (report, error) {
	d := c.r.d
	previous := rec.Item
	release, revision, ok := it.identity()
	reason := failure.lastError()
	if reason == "" {
		reason = "the update failed"
	}
	if previous == nil || !ok {
		return failure, nil
	}
	prevRelease, prevRevision, prevOK := previous.identity()
	if !prevOK || (prevRelease == release && prevRevision == revision) {
		return failure, nil
	}
	d.Log.Warn("update failed; rolling back", "workload", it.key(), "reason", reason, "to", previous.version())
	restored, err := c.restore(ctx, previous)
	if err != nil {
		return nil, err
	}
	if restored.status() != statusInSync {
		return failed(it.key(), fmt.Sprintf("%s; rolling back to %s also failed: %s", reason, previous.version(), restored.lastError())), nil
	}
	if err := c.clearStop(it.key()); err != nil {
		d.Log.Error("clear the stopped state of a workload", "workload", it.key(), "err", err)
	}
	back := &failedRelease{ReleaseID: release, Revision: revision, Reason: reason}
	if err := c.r.applied.save(record{key: it.key(), release: prevRelease, revision: prevRevision, item: previous, rolledBackFrom: back}); err != nil {
		d.Log.Error("record the rollback", "workload", it.key(), "err", err)
	}
	c.applied[it.key()] = workload{ReleaseID: prevRelease, Revision: prevRevision, Item: previous, RolledBackFrom: back}
	return rolledBack(it, previous, restored.health(), reason), nil
}
