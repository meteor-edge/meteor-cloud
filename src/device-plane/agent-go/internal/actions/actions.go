// Package actions is the module that runs one-off actions ordered from the
// console: reboot, shutdown, set a user's password, add a user, update the
// agent. When the heartbeat says actions wait, it collects them (the only
// moment a password travels), hands each to the root helper, and reports what
// happened. Each action is handed out once, so every result is kept in the
// outbox until the server accepted it, across restarts and reboots. Passwords
// are never logged or stored. update_agent starts the updater through the
// helper and is reported from the updater's outcome file once it settled.
package actions

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
)

// name is the module's id.
const name = "actions"

// KindReport is the outbox kind of action results.
const KindReport = "action_report"

// Timeouts of one action and one round.
const (
	actionTimeout = 2 * time.Minute
	maxError      = 500
	maxOutput     = 16000
	// putRetryEvery is how long put waits between outbox write attempts.
	putRetryEvery = time.Second
)

// Module runs actions.
type Module struct{}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return false }

// Run implements agent.Module: send what an earlier run left in the outbox,
// then collect actions whenever the heartbeat says some wait, retrying the
// reports with backoff while the server does not take them.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	waiting := d.Signals.Subscribe(agent.Is(agent.WorkWaiting{Kind: "actions"}))
	defer waiting.Close()
	r := &runner{d: d, helper: d.Helper, backoff: api.NewBackoff()}
	if d.Helper == nil {
		r.helper = noHelper{}
	}
	// settled checks a started agent update: at once (the updater restarted
	// this agent) and every settleEvery while one runs.
	settled := time.NewTimer(0)
	defer settled.Stop()
	timer := time.NewTimer(time.Hour)
	defer timer.Stop()
	arm := func(retry time.Duration) {
		timer.Stop()
		if retry > 0 {
			timer.Reset(retry)
		}
	}
	arm(r.flush(ctx))
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-waiting.C():
			waiting.Take()
			r.collect(ctx)
			settled.Reset(settleEvery)
			arm(r.flush(ctx))
		case <-settled.C:
			if r.settle(ctx) {
				settled.Reset(settleEvery)
			}
			arm(r.flush(ctx))
		case <-timer.C:
			arm(r.flush(ctx))
		}
	}
}

// runner is one Run's state.
type runner struct {
	d       agent.Deps
	helper  caller
	backoff *api.Backoff
	// held are results that could not be written to the outbox yet (disk full).
	// flush retries them before sending anything to the server.
	held []api.ActionResult
	// restarts are modules restart_module asked for. flush publishes them
	// once every result is in the outbox: restarting the actions module
	// cancels this Run, and its report must not be lost with it.
	restarts []string
	// clock replaces time.Now in tests.
	clock func() time.Time
}

func (r *runner) now() time.Time {
	if r.clock != nil {
		return r.clock()
	}
	return time.Now()
}

func (r *runner) env() env {
	return env{
		d: r.d, helper: r.helper, state: r.d.State, version: r.d.Version, now: r.now,
		restart: func(name string) error {
			if _, ok := r.d.Health.Get(name); !ok {
				return fmt.Errorf("restart_module: this agent has no module %q", name)
			}
			r.restarts = append(r.restarts, name)
			return nil
		},
		diagnose: func(ctx context.Context) string { return agent.Diagnose(ctx, r.d, r.helper).Text() },
	}
}

// collect fetches the waiting actions and runs them in the order the server
// gave, as the Python agent does: the contract suite records that order (a
// reboot first is reported on its own before it runs). The server hands each
// action out once; passwords stay only in this list.
func (r *runner) collect(ctx context.Context) {
	d := r.d
	token, err := d.Identity.Token()
	if err != nil {
		return // not enrolled or refused: identity deals with it, the heartbeat signals again
	}
	list, err := d.API.Actions(ctx, token)
	switch {
	case api.IsAuth(err):
		d.Identity.AuthFailed(token)
		return
	case api.StatusOf(err) == 404:
		return // an older server without actions
	case err != nil:
		d.Log.Warn("could not collect actions; trying again after the next heartbeat", "err", err)
		return
	}
	for _, a := range list {
		k, ok := registry[a.Kind]
		if !ok {
			r.put(ctx, a, unsupported("Unsupported action kind: "+pyRepr(a.Kind)))
			continue
		}
		if k.reportFirst {
			// Tell the server first, then do it, so the answer survives the reboot.
			// Require a durable outbox write before starting a power operation.
			if err := r.put(ctx, a, nil); err != nil {
				d.Log.Error("could not keep the done report; not starting the power action", "kind", a.Kind, "id", a.ID, "err", err)
				continue
			}
			r.flush(ctx)
		}
		actx, cancel := context.WithTimeout(ctx, actionTimeout)
		var output string
		var err error
		if k.output != nil {
			output, err = k.output(actx, r.env(), a)
		} else {
			err = k.run(actx, r.env(), a)
		}
		cancel()
		switch {
		case err != nil:
			d.Log.Error("action failed", "kind", a.Kind, "id", a.ID, "err", err)
			r.put(ctx, a, err)
		case k.later:
			d.Log.Info("action started; reported when it has finished", "kind", a.Kind, "id", a.ID)
		case !k.reportFirst:
			d.Log.Info("action done", "kind", a.Kind, "id", a.ID)
			r.putOutput(ctx, a, output)
		default:
			d.Log.Info("action started", "kind", a.Kind, "id", a.ID)
		}
	}
}

// put keeps one result in the outbox. On a write failure it retains the result
// in memory for flush to retry, and returns the persistence error.
func (r *runner) put(ctx context.Context, a api.Action, err error) error {
	return r.keep(ctx, a, resultOf(a.ID, err))
}

// putOutput keeps a "done" result that carries text (run_diagnostics),
// redacted and cut to the contract's length.
func (r *runner) putOutput(ctx context.Context, a api.Action, output string) error {
	res := resultOf(a.ID, nil)
	if output != "" {
		res.Output = agent.Redact(output)
		if len(res.Output) > maxOutput {
			res.Output = res.Output[:maxOutput]
		}
	}
	return r.keep(ctx, a, res)
}

func (r *runner) keep(ctx context.Context, a api.Action, res api.ActionResult) error {
	if perr := r.writeResult(ctx, res); perr != nil {
		r.held = append(r.held, res)
		r.d.Log.Error("keep an action result", "id", a.ID, "err", perr)
		return perr
	}
	return nil
}

// resultOf is the report of one action: done, or failed with the redacted
// error cut to the contract's length.
func resultOf(id string, err error) api.ActionResult {
	res := api.ActionResult{ID: id, State: api.ActionDone}
	if err != nil {
		msg := agent.Redact(err.Error())
		if len(msg) > maxError {
			msg = msg[:maxError]
		}
		res.State, res.Error = api.ActionFailed, msg
	}
	return res
}

// writeResult stores one result, retrying once after a short wait when the
// first write fails. Further retries happen from flush via held.
func (r *runner) writeResult(ctx context.Context, res api.ActionResult) error {
	if _, err := r.d.Outbox.Put(KindReport, "", res); err == nil {
		return nil
	} else if ctx.Err() != nil {
		return err
	}
	t := time.NewTimer(putRetryEvery)
	select {
	case <-ctx.Done():
		t.Stop()
		return ctx.Err()
	case <-t.C:
	}
	_, err := r.d.Outbox.Put(KindReport, "", res)
	return err
}

// flush sends the results in the outbox, in batches the contract allows, and
// removes those the server accepted. It returns how long to wait before trying
// again, 0 when nothing is left.
func (r *runner) flush(ctx context.Context) time.Duration {
	d := r.d
	for len(r.held) > 0 {
		if err := r.writeResult(ctx, r.held[0]); err != nil {
			d.Log.Error("keep a held action result", "id", r.held[0].ID, "err", err)
			return r.backoff.Next(err)
		}
		r.held = r.held[1:]
	}
	for _, name := range r.restarts {
		d.Signals.Publish(agent.RestartModule{Name: name})
	}
	r.restarts = nil
	for {
		pending, err := d.Outbox.Pending(KindReport)
		if err != nil {
			d.Log.Error("read action results", "err", err)
			return r.backoff.Next(err)
		}
		if len(pending) == 0 {
			r.backoff.Reset()
			return 0
		}
		token, err := d.Identity.Token()
		if err != nil {
			return r.backoff.Next(err)
		}
		batch := pending[:min(len(pending), api.MaxActionResults)]
		results := make([]api.ActionResult, 0, len(batch))
		ids := make([]string, 0, len(batch))
		for _, rep := range batch {
			var res api.ActionResult
			if err := json.Unmarshal(rep.Body, &res); err != nil {
				d.Log.Error("drop an unreadable action result", "id", rep.ID, "err", err)
				_ = d.Outbox.Ack(rep.ID)
				continue
			}
			results = append(results, res)
			ids = append(ids, rep.ID)
		}
		if len(results) == 0 {
			continue
		}
		_, err = d.API.ReportActions(ctx, token, results)
		switch {
		case api.IsAuth(err):
			d.Identity.AuthFailed(token)
			return r.backoff.Next(err)
		case err != nil:
			wait := r.backoff.Next(err)
			if !errors.Is(err, context.Canceled) {
				d.Log.Warn("could not report action results; kept for later", "err", err, "retry_in", wait.Round(time.Second))
			}
			return wait
		}
		if err := d.Outbox.Ack(ids...); err != nil {
			d.Log.Error("remove reported action results", "err", err)
			return r.backoff.Next(err)
		}
	}
}

// noHelper fails every call (an agent started without a helper socket).
type noHelper struct{}

func (noHelper) Call(context.Context, string, any, any) error {
	return errors.New("the root helper is not available")
}

// pyRepr quotes s like Python's repr() of a plain string ('kind'), so the
// error text matches the Python agent's; anything unusual falls back to %q.
func pyRepr(s string) string {
	for _, r := range s {
		if r < 0x20 || r == 0x7f || r == '\'' || r == '\\' {
			return fmt.Sprintf("%q", s)
		}
	}
	return "'" + s + "'"
}
