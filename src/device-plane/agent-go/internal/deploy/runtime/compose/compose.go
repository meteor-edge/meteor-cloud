// Package compose is the deploy runtime for kind "docker_compose": the
// artifact is a compose file or a zip bundle with one, brought up as project
// spec.project_name in its own directory. The root helper runs docker compose.
package compose

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

// File names in a project directory.
const (
	envFile      = ".env.meteorcloud"
	overrideFile = "compose.meteorcloud.json"
)

// Runtime brings up Compose projects through the helper.
type Runtime struct {
	Helper runtime.Caller
	// Dir holds one directory per project (config.Paths.ComposeDir).
	Dir string
}

// Kind implements runtime.Runtime.
func (Runtime) Kind() string { return "docker_compose" }

func project(w runtime.Workload) (string, error) {
	return runtime.CheckName("project_name", w.Spec.Text("project_name", w.Key))
}

// Apply implements runtime.Runtime.
//
// spec.env feeds ${VAR} substitution in the compose file. For a workload bound
// to an application, the METEORCLOUD_* variables and the token mount are added
// to every service through an override file, so the developer's compose file
// stays as written.
func (r Runtime) Apply(ctx context.Context, w runtime.Workload) (runtime.Result, error) {
	name, err := project(w)
	if err != nil {
		return runtime.Result{}, err
	}
	// The compose directory is the agent's own (0700); a project directory is
	// 0755 like the bundle in it, which containers may mount ("./config").
	dir := filepath.Join(r.Dir, name)
	if err := os.MkdirAll(r.Dir, 0o700); err != nil {
		return runtime.Result{}, fmt.Errorf("compose %s: %w", name, err)
	}
	if err := mkdirShared(r.Dir, dir); err != nil {
		return runtime.Result{}, err
	}
	file, err := materialize(w.Artifact, dir)
	if err != nil {
		return runtime.Result{}, err
	}
	req := helper.ComposeRequest{Project: name, File: file, ForceRecreate: w.ForceRecreate}
	if env := w.Spec.Env("env"); len(env) > 0 {
		req.EnvFile = filepath.Join(dir, envFile)
		if err := config.WriteFileAtomic(req.EnvFile, envLines(env), 0o600); err != nil {
			return runtime.Result{}, fmt.Errorf("compose %s: %w", name, err)
		}
	}
	override := filepath.Join(dir, overrideFile)
	if w.App != nil {
		services, err := r.services(ctx, req)
		if err != nil {
			return runtime.Result{}, err
		}
		if err := writeOverride(override, services, w); err != nil {
			return runtime.Result{}, fmt.Errorf("compose %s: %w", name, err)
		}
		req.OverrideFile = override
	} else if err := os.Remove(override); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return runtime.Result{}, fmt.Errorf("compose %s: %w", name, err)
	}
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "compose.up", req, &out); err != nil {
		return runtime.Result{}, err
	}
	if !out.OK {
		return runtime.Result{}, runtime.Failf("docker compose up %s failed: %s", name, detail(out.Output))
	}
	h := r.Check(ctx, w)
	if h.Status == runtime.Unhealthy || h.Status == runtime.Missing {
		return runtime.Result{}, runtime.Failf("%s after apply.", h.Problem)
	}
	return runtime.Result{Health: h}, nil
}

// envLines writes KEY=VALUE lines in a stable order.
func envLines(env map[string]string) []byte {
	keys := make([]string, 0, len(env))
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b bytes.Buffer
	for _, k := range keys {
		b.WriteString(k + "=" + env[k] + "\n")
	}
	return b.Bytes()
}

// writeOverride adds the app's environment and token mount to every service.
// JSON is valid YAML, so no YAML library is needed.
func writeOverride(path string, services []string, w runtime.Workload) error {
	// Only the METEORCLOUD_* variables go to the services; a release may set
	// its own endpoint. The rest of spec.env stays substitution input.
	own := map[string]string{}
	if ep, ok := w.Spec.Env("env")["METEORCLOUD_ENDPOINT"]; ok {
		own["METEORCLOUD_ENDPOINT"] = ep
	}
	addition := map[string]any{
		"environment": runtime.BoundEnv(own, w.App),
		"volumes":     []string{w.App.TokenFile + ":" + runtime.TokenMountPath + ":ro"},
	}
	all := map[string]any{}
	for _, s := range services {
		all[s] = addition
	}
	data, err := json.MarshalIndent(map[string]any{"services": all}, "", "  ")
	if err != nil {
		return err
	}
	return config.WriteFileAtomic(path, append(data, '\n'), 0o600)
}

func (r Runtime) services(ctx context.Context, req helper.ComposeRequest) ([]string, error) {
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "compose.services", req, &out); err != nil {
		return nil, err
	}
	var services []string
	for _, line := range strings.Split(out.Output, "\n") {
		if s := strings.TrimSpace(line); s != "" {
			services = append(services, s)
		}
	}
	if !out.OK || len(services) == 0 {
		msg := "no services found"
		if !out.OK {
			msg = detail(out.Output)
		}
		return nil, runtime.Failf("docker compose config --services failed: %s", msg)
	}
	return services, nil
}

// psRow is one line of `docker compose ps --format json`.
type psRow struct {
	Service  string `json:"Service"`
	Name     string `json:"Name"`
	State    string `json:"State"`
	ExitCode int    `json:"ExitCode"`
	Health   string `json:"Health"`
}

// Check implements runtime.Runtime from one `docker compose ps`: every service
// running and passing its HEALTHCHECK (one-shot jobs that exited 0 are fine).
func (r Runtime) Check(ctx context.Context, w runtime.Workload) runtime.Health {
	name, err := project(w)
	if err != nil {
		return runtime.Health{Status: runtime.Unhealthy, Problem: err.Error()}
	}
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "compose.ps", helper.ComposeRequest{Project: name}, &out); err != nil {
		return runtime.Health{Status: runtime.Unhealthy, Problem: "compose project " + name + " could not be listed"}
	}
	var rows []psRow
	if out.OK {
		rows = parsePS(out.Output)
	}
	if len(rows) == 0 {
		return runtime.Health{Status: runtime.Missing, Problem: "compose project " + name + " has no services"}
	}
	var starting *runtime.Health
	running := 0
	for _, s := range rows {
		label := s.Service
		if label == "" {
			label = s.Name
		}
		subject := "compose service " + label
		if s.State == "exited" && s.ExitCode == 0 {
			continue // a one-shot job (migration, init) that finished
		}
		if s.State != "running" {
			return runtime.Health{Status: runtime.Unhealthy, Problem: fmt.Sprintf("%s is not running (%s, exit %d)", subject, s.State, s.ExitCode)}
		}
		running++
		if s.Health == "unhealthy" {
			return runtime.Health{Status: runtime.Unhealthy, Problem: subject + " HEALTHCHECK failed"}
		}
		if s.Health == "starting" && starting == nil {
			starting = &runtime.Health{Status: runtime.Unknown, Problem: subject + " HEALTHCHECK starting"}
		}
	}
	if running == 0 {
		return runtime.Health{Status: runtime.Unhealthy, Problem: "compose project " + name + " has no running services"}
	}
	if starting != nil {
		return *starting
	}
	return runtime.Health{Status: runtime.Healthy}
}

// parsePS reads a JSON array (older Compose) or one object per line.
func parsePS(output string) []psRow {
	text := strings.TrimSpace(output)
	var rows []psRow
	if strings.HasPrefix(text, "[") {
		if json.Unmarshal([]byte(text), &rows) != nil {
			return nil
		}
		return rows
	}
	for _, line := range strings.Split(text, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var row psRow
		if json.Unmarshal([]byte(line), &row) != nil {
			return nil
		}
		rows = append(rows, row)
	}
	return rows
}

// Remove implements runtime.Runtime: docker compose down.
func (r Runtime) Remove(ctx context.Context, w runtime.Workload) error {
	name, err := project(w)
	if err != nil {
		return err
	}
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "compose.down", helper.ComposeRequest{Project: name}, &out); err != nil {
		return err
	}
	if !out.OK {
		return runtime.Failf("docker compose down %s failed: %s", name, detail(out.Output))
	}
	return nil
}

func detail(output string) string {
	if s := strings.TrimSpace(output); s != "" {
		if len(s) > 400 {
			s = s[:400]
		}
		return s
	}
	return "no output"
}
