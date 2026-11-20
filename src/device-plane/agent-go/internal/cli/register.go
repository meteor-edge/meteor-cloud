package cli

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/localrpc"
)

// envToken is the registration token variable the Python CLI read.
const envToken = "METEORCLI_TOKEN"

// runRegister is `meteorcli register`: the agent enrolls with a one-time
// registration token and starts working at once. Needs meteorcli-admin, not sudo.
func runRegister(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "register", "[--token TOKEN | --token-file PATH] [--name NAME]")
	token := fl.String("token", "", "Registration token (or $"+envToken+").")
	tokenFile := fl.String("token-file", "", "Read the registration token from a file (recommended); removed after success.")
	name := fl.String("name", "", "Optional device name.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	if *token != "" && *tokenFile != "" {
		fmt.Fprintln(env.Stderr, "error: use either --token or --token-file, not both.")
		return 2
	}
	value := strings.TrimSpace(*token)
	if *tokenFile != "" {
		data, err := os.ReadFile(*tokenFile)
		if errors.Is(err, fs.ErrNotExist) {
			fmt.Fprintf(env.Stderr, "error: token file not found: %s\n", *tokenFile)
			return 2
		}
		if err != nil {
			fmt.Fprintf(env.Stderr, "error: %v\n", err)
			return 2
		}
		value = strings.TrimSpace(string(data))
	}
	if value == "" {
		value = strings.TrimSpace(env.Getenv(envToken))
	}
	if value == "" {
		fmt.Fprintf(env.Stderr, "error: a registration token is required (use --token, --token-file, or %s).\n", envToken)
		return 2
	}

	var res identity.Enrollment
	err := localrpc.Call(ctx, env.Paths.AgentSocket(), "register", agent.RegisterParams{Token: value, Name: *name}, &res)
	if err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return exitFor(err)
	}
	if *tokenFile != "" {
		if err := os.Remove(*tokenFile); err != nil && !errors.Is(err, fs.ErrNotExist) {
			fmt.Fprintf(env.Stderr, "warning: could not remove %s: %v\n", *tokenFile, err)
		}
	}
	printEnrolled(env, res)
	return 0
}

// printEnrolled prints a new identity and what happens next.
func printEnrolled(env *Env, e identity.Enrollment) {
	fmt.Fprintf(env.Stdout, "Registered device %s\n", e.DeviceID)
	fmt.Fprintf(env.Stdout, "Organization:   %s\n", orDash(e.OrganizationID))
	fmt.Fprintf(env.Stdout, "Name:           %s\n", orDash(e.Name))
	fmt.Fprintf(env.Stdout, "Heartbeat:      every %ds\n", e.HeartbeatSeconds)
	fmt.Fprintln(env.Stdout)
	fmt.Fprintln(env.Stdout, "The agent is connecting now (heartbeats, MQTT, WireGuard). Follow it with: journalctl -u meteorcli -f")
}

// agentError explains a failed socket call in words a person can act on.
func agentError(err error) string {
	switch localrpc.CodeOf(err) {
	case localrpc.CodeDenied:
		return deniedMessage(err)
	case localrpc.CodeUnavailable:
		return "the agent is not running (systemctl status meteorcli)."
	}
	var e *localrpc.Error
	if errors.As(err, &e) {
		return e.Message
	}
	return err.Error()
}

// Exit codes for scripts (first boot), the same as the Python CLI.
const (
	exitKeyRejected = 3  // wrong, revoked or expired key: retrying cannot help
	exitRetryLater  = 75 // EX_TEMPFAIL: network down, server busy or rate limited
)

// exitFor maps an agent error to an exit code: 2 when the device is not set up
// for the command (no server, no key, nothing pending), 1 otherwise.
func exitFor(err error) int {
	switch localrpc.CodeOf(err) {
	case identity.CodeNotConfigured, identity.CodeNoAPIKey, identity.CodeAlreadyPending,
		identity.CodeNoPending, identity.CodeAlreadyEnrolled, localrpc.CodeBadRequest:
		return 2
	}
	return 1
}
