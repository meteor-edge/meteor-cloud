package cli

import (
	"context"
	"encoding/json"
	"fmt"

	"meteorcloud/meteorcli/internal/doctor"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runDoctor is `meteorcli doctor`: the agent checks the server, enrollment,
// heartbeat, MQTT, the root helper, disk space and every module, and says what
// to do about each problem. The console's "Run diagnostics" shows the same
// report. Needs the meteorcli group (or root); exits 1 when a check failed.
func runDoctor(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "doctor", "[--json]")
	asJSON := fl.Bool("json", false, "Print the report as JSON.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	var r doctor.Report
	if err := localrpc.Call(ctx, env.Paths.AgentSocket(), "doctor", nil, &r); err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		if localrpc.CodeOf(err) == localrpc.CodeUnavailable {
			fmt.Fprintln(env.Stderr, "hint: 'sudo systemctl start meteorcli', then 'meteorcli logs --source meteorcli'.")
		}
		return 1
	}
	if *asJSON {
		enc := json.NewEncoder(env.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(r); err != nil {
			fmt.Fprintf(env.Stderr, "error: %v\n", err)
			return 1
		}
	} else {
		fmt.Fprint(env.Stdout, r.Text())
	}
	if !r.OK() {
		return 1
	}
	return 0
}
