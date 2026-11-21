package helper

import (
	"context"
	"encoding/json"
	"path/filepath"
	"regexp"

	"meteorcloud/meteorcli/internal/localrpc"
)

// projectRe is a Compose project name (Compose allows no dots or capitals).
var projectRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,62}$`)

// ComposeRequest is the params of the compose.* operations. Files must be in
// the project's own directory under the agent's compose directory, where the
// deploy module writes only verified artifacts.
type ComposeRequest struct {
	Project string `json:"project"`
	// File is the compose file (compose.services, compose.up).
	File string `json:"file,omitempty"`
	// EnvFile feeds ${VAR} substitution (optional).
	EnvFile string `json:"env_file,omitempty"`
	// OverrideFile adds the MeteorCloud env and token mount to every service (compose.up).
	OverrideFile string `json:"override_file,omitempty"`
	// ForceRecreate recreates containers although nothing changed (compose.up).
	ForceRecreate bool `json:"force_recreate,omitempty"`
}

func auditCompose(raw json.RawMessage) []any {
	var r ComposeRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"project", r.Project}
}

// base returns `compose -p P -f F [--env-file E]`, checking every path.
func (r *ComposeRequest) base(h Host, needFile bool) ([]string, error) {
	if !projectRe.MatchString(r.Project) {
		return nil, badRequest("%q is not a compose project name", r.Project)
	}
	args := []string{"compose", "-p", r.Project}
	if !needFile {
		return args, nil
	}
	dir := filepath.Join(h.ComposeDir, r.Project)
	file, err := within(dir, r.File, 1)
	if err != nil {
		return nil, err
	}
	args = append(args, "-f", file)
	if r.EnvFile != "" {
		env, err := within(dir, r.EnvFile, 1)
		if err != nil {
			return nil, err
		}
		args = append(args, "--env-file", env)
	}
	return args, nil
}

func (h Host) composeServices(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r ComposeRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	args, err := r.base(h, true)
	if err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, append(args, "config", "--services")...)
}

func (h Host) composeUp(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r ComposeRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	args, err := r.base(h, true)
	if err != nil {
		return nil, err
	}
	if r.OverrideFile != "" {
		override, err := within(filepath.Join(h.ComposeDir, r.Project), r.OverrideFile, 1)
		if err != nil {
			return nil, err
		}
		args = append(args, "-f", override)
	}
	args = append(args, "up", "-d", "--remove-orphans")
	if r.ForceRecreate {
		args = append(args, "--force-recreate")
	}
	return h.dockerRun(ctx, args...)
}

func (h Host) composePS(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r ComposeRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	args, err := r.base(h, false)
	if err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, append(args, "ps", "-a", "--format", "json")...)
}

func (h Host) composeDown(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r ComposeRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	args, err := r.base(h, false)
	if err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, append(args, "down", "--remove-orphans")...)
}
