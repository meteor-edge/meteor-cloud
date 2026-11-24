package deploy

import (
	"errors"
	"fmt"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Software report states (common.json#/$defs/software_report_item).
const (
	statusInSync     = "in_sync"
	statusUpdating   = "updating"
	statusFailed     = "failed"
	statusRolledBack = "rolled_back"
)

// maxError bounds last_error, as the Python agent did (the API takes 2000).
const maxError = 500

// report is one software_report item. It is a map because which keys are
// present matters: in_sync sends last_error null to clear an old error, a
// failure sends no installed release.
type report map[string]any

func (r report) status() string { s, _ := r["sync_status"].(string); return s }
func (r report) health() string { s, _ := r["health"].(string); return s }
func (r report) lastError() string {
	s, _ := r["last_error"].(string)
	return s
}

func short(msg string) string {
	if len(msg) > maxError {
		return msg[:maxError]
	}
	return msg
}

// nullable is msg, or JSON null when it is empty.
func nullable(msg string) any {
	if msg == "" {
		return nil
	}
	return short(msg)
}

func failed(key, msg string) report {
	return report{
		"workload_key": key, "sync_status": statusFailed, "progress_percent": 0,
		"health": runtime.Unhealthy, "last_error": short(msg),
	}
}

func updating(key string, percent int) report {
	return report{"workload_key": key, "sync_status": statusUpdating, "progress_percent": percent, "health": runtime.Unknown}
}

// inSync says the item is installed. health is healthy, unhealthy or unknown.
func inSync(it item, health, problem string) report {
	return report{
		"workload_key": it.key(), "installed_release_id": it["release_id"], "installed_version": it["release_version"],
		"sync_status": statusInSync, "progress_percent": 100, "health": health, "last_error": nullable(problem),
	}
}

// inSyncFrom is inSync from a runtime health; nil when the workload is missing
// (the caller installs it again).
func inSyncFrom(it item, h runtime.Health) report {
	switch h.Status {
	case runtime.Missing:
		return nil
	case runtime.Healthy:
		return inSync(it, runtime.Healthy, h.Problem)
	case runtime.Unknown:
		return inSync(it, runtime.Unknown, h.Problem)
	}
	return inSync(it, runtime.Unhealthy, h.Problem)
}

// rolledBack says the previous release runs again because it failed.
func rolledBack(it, previous item, health, reason string) report {
	version := previous.version()
	if version == "" {
		version, _, _ = previous.identity()
	}
	return report{
		"workload_key": it.key(), "installed_release_id": previous["release_id"],
		"installed_version": previous["release_version"], "sync_status": statusRolledBack, "progress_percent": 100,
		"health": health, "last_error": short(fmt.Sprintf("Rolled back to %s: %s", version, reason)),
	}
}

// message is the text of err for the console: the server's message for an API
// error, the text itself otherwise.
func message(err error) string {
	var e *api.Error
	if errors.As(err, &e) {
		return e.Message
	}
	return err.Error()
}
