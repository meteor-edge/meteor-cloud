package deploy

import (
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/state"
)

// Installed is one workload this device runs, as the workloads module and the
// workload actions see it.
type Installed struct {
	Key  string
	Kind string
	// Name is the container, compose project or unit.
	Name      string
	ReleaseID string
	Version   string
	// Started is false for a unit installed without starting it.
	Started bool
}

// nameKeys is the spec field that names a kind's container, project or unit.
var nameKeys = map[string]string{"docker": "container_name", "docker_compose": "project_name", "systemd": "unit_name"}

func installedFrom(it item) Installed {
	return Installed{Key: it.key(), Kind: it.kind(), Name: it.spec().Text(nameKeys[it.kind()], it.key()),
		ReleaseID: runtime.Stringify(it["release_id"]), Version: it.version(), Started: it.spec().Bool("start", true)}
}

// InstalledWorkloads returns what the applied state says this device runs, by key.
func InstalledWorkloads(store *state.Store) (map[string]Installed, error) {
	all, err := appliedState{store: store}.load()
	if err != nil {
		return nil, err
	}
	out := map[string]Installed{}
	for key, w := range all {
		if w.Item == nil || nameKeys[w.Item.kind()] == "" {
			continue
		}
		in := installedFrom(w.Item)
		in.Key = key
		out[key] = in
	}
	return out, nil
}
