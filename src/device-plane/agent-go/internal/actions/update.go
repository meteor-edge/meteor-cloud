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
	// updateTimeout matches the console's plan timeout: after it the action
	// is reported failed even without an outcome (the updater died).
	updateTimeout = 30 * time.Minute
)

// startUpdate records the update_agent action, then asks the helper to start
// the updater. The updater restarts this agent, so the result is reported by
// settle, from the outcome file, once the update has finished (or rolled back).
func startUpdate(ctx context.Context, e env, a api.Action) error {
	if e.state == nil {
		return errors.New("the agent has no state directory")
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
		if !errors.Is(err, fs.ErrNotExist) {
			log.Error("read the pending agent update", "err", err)
		}
		return false
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
	for _, name := range []string{update.OutcomeFile, update.PendingFile} {
		if err := st.Remove(name); err != nil {
			log.Error("remove a settled agent update file", "file", name, "err", err)
		}
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
