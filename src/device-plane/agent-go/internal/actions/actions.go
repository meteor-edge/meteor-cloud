// Package actions is the module that runs one-off actions ordered from the
// console: reboot, shutdown, set a user's password, add a user, update the
// agent. When the heartbeat says actions wait, it collects them (the only
// moment a password travels), hands each to the root helper, and reports what
// happened. Each action is handed out once, so every result is kept in the
// outbox until the server accepted it, across restarts and reboots. Passwords
// are never logged or stored.
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
}

// collect fetches the waiting actions and runs them in order.
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
			r.put(a, unsupported("Unsupported action kind: "+pyRepr(a.Kind)))
			continue
		}
		if k.reportFirst {
			// Tell the server first, then do it, so the answer survives the reboot.
			// If the server cannot be told now, the outbox sends it after the boot.
			r.put(a, nil)
			r.flush(ctx)
		}
		actx, cancel := context.WithTimeout(ctx, actionTimeout)
		err := k.run(actx, r.helper, a)
		cancel()
		switch {
		case err != nil:
			d.Log.Error("action failed", "kind", a.Kind, "id", a.ID, "err", err)
			r.put(a, err)
		case !k.reportFirst:
			d.Log.Info("action done", "kind", a.Kind, "id", a.ID)
			r.put(a, nil)
		default:
			d.Log.Info("action started", "kind", a.Kind, "id", a.ID)
		}
	}
}

// put keeps one result in the outbox.
func (r *runner) put(a api.Action, err error) {
	res := api.ActionResult{ID: a.ID, State: api.ActionDone}
	if err != nil {
		msg := agent.Redact(err.Error())
		if len(msg) > maxError {
			msg = msg[:maxError]
		}
		res.State, res.Error = api.ActionFailed, msg
	}
	if _, perr := r.d.Outbox.Put(KindReport, "", res); perr != nil {
		r.d.Log.Error("keep an action result", "id", a.ID, "err", perr)
	}
}

// flush sends the results in the outbox, in batches the contract allows, and
// removes those the server accepted. It returns how long to wait before trying
// again, 0 when nothing is left.
func (r *runner) flush(ctx context.Context) time.Duration {
	d := r.d
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
