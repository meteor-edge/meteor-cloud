package cli

import (
	"context"
	"errors"
	"fmt"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runRequest is `meteorcli request-token`: the agent asks an operator to
// approve this device, then waits for the approval in the background (also
// across reboots) and connects by itself. --wait also waits here.
func runRequest(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "request-token", "[--api-key KEY] [--name NAME] [--wait SECONDS] [--new]")
	apiKey := fl.String("api-key", "", "Enrollment API key (or $"+envAPIKey+"; default: the saved key). Saved by the agent.")
	name := fl.String("name", "", "Optional requested device name.")
	wait := fl.Int("wait", 0, "Also wait this long here for the approval (the agent waits anyway).")
	renew := fl.Bool("new", false, "Submit a new request even if one is pending or the device is enrolled.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	key := normalizeSecret(*apiKey)
	if key == "" {
		key = normalizeSecret(env.Getenv(envAPIKey))
	}
	var res identity.RequestResult
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "enroll.request",
		agent.RequestParams{APIKey: key, Name: *name, New: *renew}, &res)
	if err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return requestExit(env, err)
	}
	if res.Status == identity.PollApproved && res.Enrollment != nil {
		printEnrolled(env, *res.Enrollment)
		return 0
	}
	if res.Previous != "" {
		fmt.Fprintf(env.Stderr, "The previous enrollment request was %s; submitted a new one.\n", res.Previous)
	}
	fmt.Fprintf(env.Stdout, "Enrollment request %s submitted.\n", res.RequestID)
	if *wait <= 0 {
		fmt.Fprintln(env.Stdout, "The agent waits for an administrator to approve this device and then connects.")
		fmt.Fprintln(env.Stdout, "Follow it with: journalctl -u meteorcli -f")
		return 0
	}
	fmt.Fprintf(env.Stdout, "Waiting for an administrator to approve this device (at most %ds)…\n", *wait)
	return claimAndPrint(ctx, env, *wait)
}

// requestExit maps a failed request to the exit codes first-boot scripts use.
func requestExit(env *Env, err error) int {
	var code int
	switch localrpc.CodeOf(err) {
	case identity.CodeKeyRejected:
		return exitKeyRejected
	case identity.CodeRetryLater:
		code = exitRetryLater
	default:
		return exitFor(err)
	}
	var e *localrpc.Error
	if errors.As(err, &e) && e.RetryAfter > 0 {
		// Scripts read this line to wait as long as the server asked.
		fmt.Fprintf(env.Stderr, "retry-after: %d\n", e.RetryAfter)
	}
	return code
}

// runClaim is `meteorcli claim`: report (or wait --wait seconds for) the
// approval the agent is polling for.
func runClaim(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "claim", "[--wait SECONDS]")
	wait := fl.Int("wait", 0, "Wait this long for the approval (default: one check).")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	if *wait > 0 {
		fmt.Fprintf(env.Stdout, "Checking the enrollment request (waiting up to %ds)…\n", *wait)
	}
	return claimAndPrint(ctx, env, max(*wait, 0))
}

// claimAndPrint asks the agent where the pending request stands, waiting up to
// wait seconds, and prints it. Exit 0 enrolled, 2 still pending, 1 ended.
func claimAndPrint(ctx context.Context, env *Env, wait int) int {
	limit := min(time.Duration(wait)*time.Second, agent.MaxClaimWait)
	ctx, cancel := context.WithTimeout(ctx, limit+time.Minute)
	defer cancel()
	var res identity.ClaimResult
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "enroll.claim", agent.ClaimParams{WaitSeconds: wait}, &res)
	if err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return exitFor(err)
	}
	if res.Status == identity.PollApproved && res.Enrollment != nil {
		printEnrolled(env, *res.Enrollment)
		return 0
	}
	fmt.Fprintf(env.Stdout, "Enrollment request %s is still waiting for approval.\n", res.RequestID)
	fmt.Fprintln(env.Stdout, "The agent keeps checking and connects as soon as an administrator approves it.")
	return 2
}
