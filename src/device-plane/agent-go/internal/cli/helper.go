package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"os"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
)

// helperUser is the only user the helper serves.
const helperUser = "meteor"

// runHelper is `meteorcli helper` (the root service behind meteorcli-helper.socket)
// and `meteorcli helper call <op> [json]` (send one request, for debugging and tests).
func runHelper(ctx context.Context, env *Env, args []string) int {
	if len(args) > 0 && args[0] == "call" {
		return runHelperCall(ctx, env, args[1:])
	}
	fl := newFlags(env, "helper", "[--listen PATH] | call <operation> [json]")
	listen := fl.String("listen", "", "Listen on PATH instead of the socket systemd passes (development).")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	log := agent.NewLogger(env.Stderr, slog.LevelInfo).With("module", "helper")
	if os.Geteuid() != 0 {
		log.Error("the helper must run as root (meteorcli-helper.service)")
		return 1
	}
	uid, ok := localrpc.LookupUID(helperUser)
	if !ok {
		log.Error("user " + helperUser + " does not exist; is meteorcli installed?")
		return 1
	}

	srv := &helper.Server{AllowedUID: uid, Host: helper.SystemHost(), Log: log}
	var l *net.UnixListener
	var err error
	if *listen != "" {
		l, err = net.ListenUnix("unix", &net.UnixAddr{Name: *listen, Net: "unix"})
	} else {
		l, err = helper.ListenerFromSystemd()
		srv.Idle = helper.DefaultIdle // socket-activated: systemd restarts us on demand
	}
	if err != nil {
		log.Error("listen", "err", err)
		return 1
	}
	log.Info("serving", "allowed_uid", uid)
	if err := srv.Serve(ctx, l); err != nil {
		log.Error("serve", "err", err)
		return 1
	}
	return 0
}

func runHelperCall(ctx context.Context, env *Env, args []string) int {
	if len(args) < 1 || len(args) > 2 {
		fmt.Fprintln(env.Stderr, "Usage: meteorcli helper call <operation> [json params]")
		return 2
	}
	var params json.RawMessage
	if len(args) == 2 {
		params = json.RawMessage(args[1])
		if !json.Valid(params) {
			fmt.Fprintln(env.Stderr, "error: params are not valid JSON")
			return 2
		}
	}
	var out json.RawMessage
	if err := localrpc.Call(ctx, env.Paths.HelperSocket(), args[0], params, &out); err != nil {
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
		return 1
	}
	fmt.Fprintln(env.Stdout, string(out))
	return 0
}
