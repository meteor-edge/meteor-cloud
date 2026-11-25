package helper

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
)

// The deploy module installs Docker workloads through these operations; the
// agent never gets the Docker socket. Like systemd.install_unit they are
// root-equivalent by design (a container can mount the host), so the helper
// checks the shape of every argument, builds the docker command itself, and
// reads files only from the agent's artifact and credential directories. The
// agent asks only for releases whose signature it verified.

// deployTimeout bounds a docker load, run or compose up: an image pull on a
// slow link takes minutes, a hung one is cut here.
const deployTimeout = 15 * time.Minute

// DockerHealthTemplate is the docker inspect format the docker runtime parses:
// running, status, exit code and HEALTHCHECK status. Never Config.Env, labels
// or logs, which can hold secrets.
const DockerHealthTemplate = "{{.State.Running}}|{{.State.Status}}|{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"

var (
	imageRe   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}$`) // as long as docker.pull takes
	restartRe = regexp.MustCompile(`^(no|always|unless-stopped|on-failure(:[0-9]{1,4})?)$`)
	portRe    = regexp.MustCompile(`^[0-9\[][0-9A-Za-z.:\[\]/-]{0,99}$`)
	envKeyRe  = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_.-]{0,127}$`)
)

// Limits of one docker.run request.
const (
	maxPorts   = 64
	maxEnv     = 256
	maxCommand = 128
)

// DockerPathRequest is the params of docker.load: an artifact the agent downloaded.
type DockerPathRequest struct {
	Path string `json:"path"`
}

// DockerNameRequest is the params of docker.health and docker.remove.
type DockerNameRequest struct {
	Name string `json:"name"`
}

// DockerRunRequest is the params of docker.run: replace container Name with a
// new one from Image. Env values are secrets (never logged, never on a command
// line); TokenFile, when set, is mounted read-only at /run/secrets/meteorcloud-token.
type DockerRunRequest struct {
	Name      string            `json:"name"`
	Image     string            `json:"image"`
	Restart   string            `json:"restart"`
	Ports     []string          `json:"ports"`
	Env       map[string]string `json:"env"`
	TokenFile string            `json:"token_file"`
	Command   []string          `json:"command"`
	// PullNever runs only an image already on the device (one loaded from a
	// verified artifact): docker run never pulls it from a registry.
	PullNever bool `json:"pull_never,omitempty"`
	// Workload and Release become labels: workload.* finds the container by them.
	Workload string `json:"workload,omitempty"`
	Release  string `json:"release,omitempty"`
}

func (r *DockerRunRequest) validate(h Host) error {
	switch {
	case !containerRe.MatchString(r.Name):
		return badRequest("%q is not a container name", r.Name)
	case !imageRe.MatchString(r.Image):
		return badRequest("%q is not an image reference", r.Image)
	case r.Restart != "" && !restartRe.MatchString(r.Restart):
		return badRequest("%q is not a restart policy", r.Restart)
	case r.Workload != "" && !labelRe.MatchString(r.Workload), r.Release != "" && !labelRe.MatchString(r.Release):
		return badRequest("%q, %q are not workload and release labels", r.Workload, r.Release)
	case len(r.Ports) > maxPorts || len(r.Env) > maxEnv || len(r.Command) > maxCommand:
		return badRequest("too many ports, env entries or command arguments")
	}
	for _, p := range r.Ports {
		if !portRe.MatchString(p) {
			return badRequest("%q is not a port mapping", p)
		}
	}
	for k, v := range r.Env {
		if !envKeyRe.MatchString(k) {
			return badRequest("%q is not an environment variable name", k)
		}
		if strings.ContainsAny(v, "\n\r\x00") {
			return badRequest("the value of %s contains a line break", k)
		}
	}
	for _, a := range r.Command {
		if strings.ContainsRune(a, 0) {
			return badRequest("command arguments must not contain NUL")
		}
	}
	if r.TokenFile != "" {
		if _, err := within(h.AppCredentialsDir, r.TokenFile, 2); err != nil {
			return err
		}
		if filepath.Base(r.TokenFile) != "token" {
			return badRequest("token_file must be a token file")
		}
	}
	return nil
}

// auditDockerRun logs what runs, never the environment.
func auditDockerRun(raw json.RawMessage) []any {
	var r DockerRunRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"container", r.Name, "image", r.Image, "env_keys", len(r.Env)}
}

func auditDockerName(raw json.RawMessage) []any {
	var r DockerNameRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"container", r.Name}
}

func (h Host) dockerLoad(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p DockerPathRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	path, err := within(h.ArtifactsDir, p.Path, 1)
	if err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, "load", "-i", path)
}

func (h Host) dockerHealth(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p DockerNameRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if !containerRe.MatchString(p.Name) {
		return nil, badRequest("%q is not a container name", p.Name)
	}
	return h.dockerRun(ctx, "inspect", "-f", DockerHealthTemplate, p.Name)
}

func (h Host) dockerRemove(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p DockerNameRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if !containerRe.MatchString(p.Name) {
		return nil, badRequest("%q is not a container name", p.Name)
	}
	return h.dockerRun(ctx, "rm", "-f", p.Name)
}

// dockerRunContainer removes the old container and starts the new one, like
// the Python agent: docker rm -f, then docker run -d. The environment goes in
// a private env file, not on the command line where any user could read it.
func (h Host) dockerRunContainer(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r DockerRunRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := r.validate(h); err != nil {
		return nil, err
	}
	args := []string{"run", "-d", "--name", r.Name}
	if r.PullNever {
		args = append(args, "--pull", "never")
	}
	if r.Workload != "" {
		args = append(args, "--label", LabelWorkload+"="+r.Workload)
	}
	if r.Release != "" {
		args = append(args, "--label", LabelRelease+"="+r.Release)
	}
	if r.Restart != "" {
		args = append(args, "--restart", r.Restart)
	}
	for _, p := range r.Ports {
		args = append(args, "-p", p)
	}
	// Everything that can fail is ready before the running container goes.
	if len(r.Env) > 0 {
		envFile, err := writeEnvFile(h.TempDir, r.Env)
		if err != nil {
			return nil, err
		}
		defer os.Remove(envFile)
		args = append(args, "--env-file", envFile)
	}
	if r.TokenFile != "" {
		args = append(args, "-v", r.TokenFile+":/run/secrets/meteorcloud-token:ro")
	}
	args = append(args, r.Image)
	args = append(args, r.Command...) // after the image: arguments of the container, not of docker

	_, _ = h.engineRun(ctx, "rm", "-f", r.Name) // a missing container is fine
	return h.dockerRun(ctx, args...)
}

// writeEnvFile writes KEY=VALUE lines to a new 0600 file in dir ("" for the
// helper's private /tmp) and returns its path.
func writeEnvFile(dir string, env map[string]string) (string, error) {
	f, err := os.CreateTemp(dir, "meteorcli-env-*")
	if err != nil {
		return "", err
	}
	var b strings.Builder
	for k, v := range env {
		b.WriteString(k + "=" + v + "\n")
	}
	_, werr := f.WriteString(b.String())
	cerr := f.Close()
	if err := errors.Join(werr, cerr); err != nil {
		os.Remove(f.Name())
		return "", err
	}
	return f.Name(), nil
}

// within returns path when it is an absolute, clean path depth levels below
// root (1 = a direct child) that resolves inside root, and is a regular file.
func within(root, path string, depth int) (string, error) {
	if root == "" {
		return "", badRequest("this helper has no directory for %s", path)
	}
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return "", badRequest("%q must be an absolute, clean path", path)
	}
	rel, err := filepath.Rel(root, path)
	if err != nil || strings.HasPrefix(rel, "..") || len(strings.Split(rel, string(filepath.Separator))) != depth {
		return "", badRequest("%s is not under %s", path, root)
	}
	realRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", fmt.Errorf("%s: %w", root, err)
	}
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", badRequest("%s: %v", path, err)
	}
	if rel, err := filepath.Rel(realRoot, real); err != nil || strings.HasPrefix(rel, "..") {
		return "", badRequest("%s leads outside %s", path, root)
	}
	st, err := os.Stat(real)
	if err != nil || !st.Mode().IsRegular() {
		return "", badRequest("%s is not a regular file", path)
	}
	return real, nil
}
