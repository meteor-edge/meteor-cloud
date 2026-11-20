package cli

import (
	"context"
	"fmt"

	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runTest is `meteorcli test`: the agent checks that the server answers and
// that the saved API key is valid, without creating an enrollment request.
func runTest(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "test", "")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	var res identity.TestResult
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "test", nil, &res)
	if err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		if localrpc.CodeOf(err) == identity.CodeNotConfigured {
			return 2
		}
		return 1
	}
	fmt.Fprintf(env.Stdout, "API base:       %s\n", res.Server)
	fmt.Fprintln(env.Stdout, "Server:         ok")
	switch res.APIKey {
	case "missing":
		fmt.Fprintln(env.Stdout, "API key:        MISSING")
		fmt.Fprintln(env.Stderr, "error: an API key is required (use 'meteorcli config --api-key key_…').")
		return 1
	case "rejected":
		fmt.Fprintf(env.Stderr, "API key:        rejected (%s)\n", res.APIKeyError)
		fmt.Fprintf(env.Stderr, "hint: %s does not recognize this key. Create a new key in Fleet → API keys on that same server, "+
			"then run 'meteorcli config --api-key key_…'.\n", res.Server)
		return 1
	}
	fmt.Fprintln(env.Stdout, "API key:        ok")
	org := res.OrganizationName
	if org == "" {
		org = res.OrganizationID
	}
	fmt.Fprintf(env.Stdout, "Organization:   %s\n", org)
	fmt.Fprintf(env.Stdout, "Key name:       %s\n", orDash(res.KeyName))
	if res.KeyPrefix != "" {
		fmt.Fprintf(env.Stdout, "Key prefix:     %s…\n", res.KeyPrefix)
	}
	return 0
}
