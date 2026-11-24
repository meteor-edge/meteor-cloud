package cli

import (
	"context"
	"fmt"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runModule is `meteorcli module restart <name>`: ask the running agent to
// restart (or unpark) one module. Needs the meteorcli-admin group or root.
func runModule(ctx context.Context, env *Env, args []string) int {
	if len(args) != 2 || args[0] != "restart" {
		fmt.Fprintln(env.Stderr, "Usage: meteorcli module restart <name>")
		return 2
	}
	name := args[1]
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "module.restart", agent.ModuleRestartParams{Name: name}, nil)
	switch localrpc.CodeOf(err) {
	case "":
		if err != nil {
			fmt.Fprintf(env.Stderr, "error: %v\n", err)
			return 1
		}
		fmt.Fprintf(env.Stdout, "Module %s restarted.\n", name)
		return 0
	case localrpc.CodeDenied:
		fmt.Fprintf(env.Stderr, "error: %s\n", deniedMessage(err))
	case localrpc.CodeUnavailable:
		fmt.Fprintln(env.Stderr, "error: the agent is not running (systemctl status meteorcli).")
	default:
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
	}
	return 1
}
