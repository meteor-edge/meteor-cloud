package actions

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/update"
)

const (
	// settleEvery is how often a started update is checked for its outcome.
	settleEvery = 10 * time.Second
	// updateTimeout: after it the action is reported failed even without an
	// outcome (the updater died).
	updateTimeout = update.ActionTimeout
)

// startUpdate records the update_agent action, then asks the helper to start
// the updater. The updater restarts this agent, so the result is reported by
// settle, from the outcome file, once the update has finished (or rolled back).
// While an earlier action is still being settled the new one fails and the
// earlier record is left alone.
func startUpdate(ctx context.Context, e env, a api.Action) error {
	if e.state == nil {
		return errors.New("the agent has no state directory")
	}
	if err := update.CheckNoPending(e.state); err != nil {
		return fmt.Errorf("another meteorcli update is running: %w", err)
	}
	p := update.Pending{ActionID: a.ID, FromVersion: e.version, StartedAt: e.now().UTC()}
	if err := e.state.Write(update.PendingFile, update.FileVersion, p); err != nil {
		return fmt.Errorf("could not keep the update action: %w", err)
	}
	if err := e.helper.Call(ctx, "update.start", helper.UpdateStartRequest{ActionID: a.ID}, nil); err != nil {
		if rerr := e.state.Remove(update.PendingFile); rerr != nil {
			return fmt.Errorf("could not start the meteorcli updater: %w (and %v)", err, rerr)
		}
		return fmt.Errorf("could not start the meteorcli updater: %w", err)
	}
	return nil
}

// settle reports a started update once its outcome is there (or it took too
// long), and returns true while an update is still running. The pending file
// is removed only after the result is safe in the outbox.
func (r *runner) settle(ctx context.Context) (running bool) {
	st, log := r.d.State, r.d.Log
	if st == nil {
		return false
	}
	var p update.Pending
	if _, err := st.Read(update.PendingFile, &p); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return false
		}
		// Keep checking: a damaged file is moved aside (the next read sees
		// none), anything else may pass.
		log.Error("read the pending agent update", "err", err)
		return true
	}
	var o update.Outcome
	_, oerr := st.Read(update.OutcomeFile, &o)
	if oerr != nil && !errors.Is(oerr, fs.ErrNotExist) {
		log.Error("read the agent update outcome", "err", oerr)
	}
	var failure error
	switch {
	case oerr == nil && o.ActionID == p.ActionID:
		if !o.Done() {
			failure = outcomeError(o)
		}
	case r.now().Sub(p.StartedAt) > updateTimeout:
		failure = errors.New("the meteorcli update did not finish in time")
	default:
		return true
	}
	if err := r.writeResult(ctx, resultOf(p.ActionID, failure)); err != nil {
		log.Error("keep the agent update result; trying again", "id", p.ActionID, "err", err)
		return true
	}
	if failure != nil {
		log.Warn("agent update failed", "id", p.ActionID, "status", o.Status, "err", failure)
	} else {
		log.Info("agent update done", "id", p.ActionID, "status", o.Status, "from", o.FromVersion, "to", o.ToVersion)
	}
	// Pending first: an outcome without a pending record is ignored, a pending
	// record without its outcome would time out and be reported again.
	if err := st.Remove(update.PendingFile); err != nil {
		log.Error("remove the settled agent update; trying again", "err", err)
		return true
	}
	if err := st.Remove(update.OutcomeFile); err != nil {
		log.Error("remove the agent update outcome", "err", err)
	}
	return false
}

// outcomeError is the console's text for a failed update.
func outcomeError(o update.Outcome) error {
	if o.Error != "" {
		return errors.New(o.Error)
	}
	return fmt.Errorf("meteorcli update ended with %q", o.Status)
}
