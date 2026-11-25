package helper

import (
	"context"
	"io"
)

// Engine is the container engine the docker.* and compose.* operations use.
// The zero value is Docker at its default address.
type Engine struct {
	// Name is the engine's id ("docker").
	Name string
	// CLI is the command to run; it must be a bare name (see lookTool).
	CLI string
	// Endpoint is the engine's API address, "" for the CLI's default.
	Endpoint string
}

// engineArgs returns the command and arguments for an engine call.
func (h Host) engineArgs(args ...string) (string, []string) {
	cli := h.Engine.CLI
	if cli == "" {
		cli = "docker"
	}
	if h.Engine.Endpoint != "" {
		args = append([]string{"--host", h.Engine.Endpoint}, args...)
	}
	return cli, args
}

// engineRun runs the engine's CLI and returns its combined output.
func (h Host) engineRun(ctx context.Context, args ...string) ([]byte, error) {
	cli, args := h.engineArgs(args...)
	return h.Run(ctx, cli, args...)
}

// engineStream runs the engine's CLI and writes its stdout to w.
func (h Host) engineStream(ctx context.Context, w io.Writer, args ...string) error {
	cli, args := h.engineArgs(args...)
	return h.Stream(ctx, w, cli, args...)
}

// engineLines runs the engine's CLI and returns its output's lines.
func (h Host) engineLines(ctx context.Context, args ...string) []string {
	cli, args := h.engineArgs(args...)
	return h.lines(ctx, cli, args...)
}
