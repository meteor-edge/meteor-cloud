package update

import (
	"time"
)

// State files (in /var/lib/meteorcli/state) that tie an update_agent action to
// the updater's result. The agent writes Pending before it asks the helper to
// start the updater; the updater (root) writes Outcome when it is finished and
// hands it to the meteor user; the actions module reports the action from them.
const (
	PendingFile = "agent-update.json"
	OutcomeFile = "agent-update-outcome.json"
	// FileVersion is the format of both files.
	FileVersion = 1
)

// Outcome statuses. updated and already_current report the action done.
const (
	StatusUpdated        = "updated"
	StatusAlreadyCurrent = "already_current"
	StatusFailed         = "failed"      // nothing was changed
	StatusRolledBack     = "rolled_back" // the new build was installed, failed, and the previous one is back
)

// Pending is an update_agent action whose updater was started.
type Pending struct {
	ActionID    string    `json:"action_id"`
	FromVersion string    `json:"from_version"`
	StartedAt   time.Time `json:"started_at"`
}

// Outcome is what the updater did. ActionID is empty for an update started
// from the command line.
type Outcome struct {
	ActionID    string    `json:"action_id"`
	Status      string    `json:"status"`
	FromVersion string    `json:"from_version"`
	ToVersion   string    `json:"to_version"`
	Error       string    `json:"error"`
	FinishedAt  time.Time `json:"finished_at"`
}

// Done reports whether the outcome counts as a successful update_agent.
func (o Outcome) Done() bool {
	return o.Status == StatusUpdated || o.Status == StatusAlreadyCurrent
}
