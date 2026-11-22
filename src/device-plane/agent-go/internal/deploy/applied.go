package deploy

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"

	"meteorcloud/meteorcli/internal/state"
)

// appliedFile is the state file of what is installed per workload: the
// release and revision (so an unchanged item is not applied again), the
// applied item (what a failed update rolls back to), a release this device
// rolled back from, and whether the workload must be applied again. The kept
// item can hold the release's env values; state files are 0600.
const appliedFile = "applied-software.json"

const appliedVersion = 1

// workload is what is recorded for one workload.
type workload struct {
	ReleaseID string `json:"release_id"`
	Revision  int    `json:"revision"`
	Item      item   `json:"item,omitempty"`
	// RolledBackFrom is a release that failed its health check: it is not tried
	// again until the desired release changes.
	RolledBackFrom *failedRelease `json:"rolled_back_from,omitempty"`
	// Reapply means the running workload no longer matches what was applied
	// (its token file was lost). It stays set until an apply succeeds.
	Reapply bool `json:"reapply,omitempty"`

	// extra holds the fields this version does not know, written back as read.
	extra map[string]json.RawMessage
}

// workloadFields are the JSON names of workload's own fields.
var workloadFields = []string{"release_id", "revision", "item", "rolled_back_from", "reapply"}

// UnmarshalJSON implements json.Unmarshaler, keeping unknown fields.
func (w *workload) UnmarshalJSON(data []byte) error {
	type plain workload
	var p plain
	if err := json.Unmarshal(data, &p); err != nil {
		return err
	}
	var all map[string]json.RawMessage
	if err := json.Unmarshal(data, &all); err != nil {
		return err
	}
	for _, k := range workloadFields {
		delete(all, k)
	}
	if len(all) > 0 {
		p.extra = all
	}
	*w = workload(p)
	return nil
}

// MarshalJSON implements json.Marshaler, writing unknown fields back.
func (w workload) MarshalJSON() ([]byte, error) {
	type plain workload
	own, err := json.Marshal(plain(w))
	if err != nil || len(w.extra) == 0 {
		return own, err
	}
	var all map[string]json.RawMessage
	if err := json.Unmarshal(own, &all); err != nil {
		return nil, err
	}
	for k, v := range w.extra {
		if _, ok := all[k]; !ok {
			all[k] = v
		}
	}
	return json.Marshal(all)
}

type failedRelease struct {
	ReleaseID string `json:"release_id"`
	Revision  int    `json:"revision"`
	Reason    string `json:"reason"`
}

// appliedState reads and writes appliedFile.
type appliedState struct {
	store *state.Store
}

// load returns every recorded workload. A missing or damaged file is empty
// (a damaged one was moved aside by the store).
func (a appliedState) load() (map[string]workload, error) {
	var raw map[string]json.RawMessage
	version, err := a.store.Read(appliedFile, &raw)
	switch {
	case errors.Is(err, fs.ErrNotExist), errors.Is(err, state.ErrCorrupt):
		return map[string]workload{}, nil
	case err != nil:
		return nil, err
	}
	out := map[string]workload{}
	if version < 1 {
		return out, nil
	}
	if w, ok := raw["workloads"]; ok {
		if err := json.Unmarshal(w, &out); err != nil {
			return map[string]workload{}, nil
		}
	}
	return out, nil
}

// record is one change to a workload's entry.
type record struct {
	key      string
	release  string
	revision int
	// item replaces the kept item when set.
	item item
	// rolledBackFrom records a device-side rollback (and may lower the revision).
	rolledBackFrom *failedRelease
	// reapply marks the workload for another apply.
	reapply bool
}

// save applies r to the file: the revision is a high-water mark that only a
// rollback may lower.
func (a appliedState) save(r record) error {
	all, err := a.load()
	if err != nil {
		return err
	}
	prev, had := all[r.key]
	release, revision := r.release, r.revision
	if r.rolledBackFrom == nil && had && prev.Revision > revision {
		release, revision = prev.ReleaseID, prev.Revision
		if release == "" {
			release = r.release
		}
	}
	next := workload{ReleaseID: release, Revision: revision, Item: prev.Item, RolledBackFrom: r.rolledBackFrom, Reapply: r.reapply,
		extra: prev.extra}
	if r.item != nil {
		next.Item = r.item
	}
	all[r.key] = next
	return a.write(all)
}

func (a appliedState) write(all map[string]workload) error {
	if err := a.store.Write(appliedFile, appliedVersion, struct {
		Workloads map[string]workload `json:"workloads"`
	}{all}); err != nil {
		return fmt.Errorf("applied software: %w", err)
	}
	return nil
}
