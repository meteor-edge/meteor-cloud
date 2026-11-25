package cli

import (
	"context"
	"fmt"

	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runUpdate is `meteorcli update`: ask the agent to start the updater (it
// asks the root helper, which starts meteorcli-update.service). No sudo: the
// meteorcli-admin group is enough. The updater restarts the agent, so this
// returns once it started; follow it in the journal.
func runUpdate(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "update", "")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	var reply helper.UpdateStartReply
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "update.start", nil, &reply)
	switch localrpc.CodeOf(err) {
	case "":
		if err != nil {
			fmt.Fprintf(env.Stderr, "error: %v\n", err)
			return 1
		}
		fmt.Fprintf(env.Stdout, "Update started (%s). The agent restarts on the new version; if it does not come up, the previous one is put back.\n", reply.Unit)
		fmt.Fprintf(env.Stdout, "Follow it with: journalctl -u %s -f\n", reply.Unit)
		return 0
	case localrpc.CodeDenied:
		fmt.Fprintf(env.Stderr, "error: %s\n", deniedMessage(err))
	case localrpc.CodeUnavailable:
		fmt.Fprintln(env.Stderr, "error: the agent is not running (systemctl status meteorcli); as root run: sudo meteorcli updater")
	default:
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
	}
	return 1
}
