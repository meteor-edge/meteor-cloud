package helper

import (
	"context"
	"encoding/json"
	"regexp"

	"meteorcloud/meteorcli/internal/localrpc"
)

// The agent watches containers for the app metrics and lifecycle events, but
// never gets the Docker socket: these read-only operations run the Python
// observer's fixed docker commands and return their output, which the agent
// parses itself (as little code as possible runs as root).

// InspectTemplate is the docker inspect format the observer parses: name,
// state, exit code, OOM kill, restarts, start time and health checks.
const InspectTemplate = "{{.Name}}|{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|" +
	"{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}|" +
	"{{range .State.Health.Log}}{{.Start.UnixNano}},{{.End.UnixNano}};{{end}}{{end}}"

// StatsFormat is the docker stats format the observer parses.
const StatsFormat = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"

// MaxContainers bounds the containers one docker.inspect looks at.
const MaxContainers = 20

// containerRe is a Docker container name; it cannot start with "-".
var containerRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

// DockerOutput is the answer of the docker.* operations. OK is false when
// docker exited non-zero; Output may still hold useful lines (inspect prints
// the containers that exist when one vanished meanwhile).
type DockerOutput struct {
	Output string `json:"output"`
	OK     bool   `json:"ok"`
}

// DockerInspectRequest is the params of docker.inspect.
type DockerInspectRequest struct {
	Names []string `json:"names"`
}

func (h Host) dockerRun(ctx context.Context, args ...string) (any, error) {
	out, err := h.Run(ctx, "docker", args...)
	if err != nil && len(out) == 0 {
		return nil, err
	}
	return DockerOutput{Output: string(out), OK: err == nil}, nil
}

func (h Host) dockerPS(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, "ps", "-a", "--format", "{{.Names}}")
}

func (h Host) dockerInspect(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p DockerInspectRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if len(p.Names) == 0 || len(p.Names) > MaxContainers {
		return nil, badRequest("names must list 1 to %d containers", MaxContainers)
	}
	for _, n := range p.Names {
		if !containerRe.MatchString(n) {
			return nil, badRequest("%q is not a container name", n)
		}
	}
	return h.dockerRun(ctx, append([]string{"inspect", "-f", InspectTemplate}, p.Names...)...)
}

func (h Host) dockerStats(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, "stats", "--no-stream", "--format", StatsFormat)
}
