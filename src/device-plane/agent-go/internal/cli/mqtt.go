package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runMQTTTest is `meteorcli mqtt-test [TOPIC] [MESSAGE]`: the agent publishes
// one message through its own MQTT session (default: a small JSON object on
// devices/{id}/status, not retained), so the CLI never needs the broker
// password. Needs the meteorcli-admin group: it speaks as the device.
func runMQTTTest(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "mqtt-test", "[TOPIC] [MESSAGE]")
	rest, code, done := parseWithArgs(fl, args, 2)
	if done {
		return code
	}
	p := agent.MQTTTestParams{}
	if len(rest) > 0 {
		p.Topic = rest[0]
	}
	if len(rest) > 1 {
		p.Payload = rest[1]
		raw := strings.TrimSpace(p.Payload)
		if (strings.HasPrefix(raw, "{") || strings.HasPrefix(raw, "[")) && !json.Valid([]byte(raw)) {
			fmt.Fprintln(env.Stderr, "error: MESSAGE looks like JSON but does not parse")
			return 1
		}
	} else {
		b, _ := json.Marshal(map[string]string{
			"status": "online", "source": "meteorcli", "message": "mqtt-test",
			"sent_at": time.Now().UTC().Format(time.RFC3339Nano),
		})
		p.Payload = string(b)
	}
	var res agent.MQTTTestReply
	if err := localrpc.Call(ctx, env.Paths.AgentSocket(), "mqtt.test", p, &res); err != nil {
		fmt.Fprintf(env.Stderr, "error: MQTT test failed: %s\n", agentError(err))
		return 1
	}
	fmt.Fprintf(env.Stdout, "Published MQTT test message to %s\n", res.Topic)
	return 0
}

// runMQTTListen is `meteorcli mqtt-listen [TOPIC] [--timeout S]`: print the
// commands this device receives, as "<topic> <payload>" lines, until Ctrl+C or
// the timeout. The agent's session receives them; the CLI asks the agent.
func runMQTTListen(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "mqtt-listen", "[TOPIC] [--timeout SECONDS]")
	timeout := fl.Float64("timeout", 0, "Stop after this many seconds (default: until Ctrl+C).")
	rest, code, done := parseWithArgs(fl, args, 1)
	if done {
		return code
	}
	topic := ""
	if len(rest) > 0 {
		topic = rest[0]
	}
	if *timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(*timeout*float64(time.Second)))
		defer cancel()
	}
	sock := env.Paths.AgentSocket()
	var after *uint64
	warned := false
	for first := true; ; first = false {
		wait := 25
		if d, ok := ctx.Deadline(); ok {
			wait = max(1, min(wait, int(time.Until(d).Seconds())))
		}
		var res agent.MQTTListenReply
		err := localrpc.Call(ctx, sock, "mqtt.listen", agent.MQTTListenParams{Topic: topic, After: after, WaitSeconds: wait}, &res)
		deadline, hasDeadline := ctx.Deadline()
		if ctx.Err() != nil || (hasDeadline && !time.Now().Before(deadline)) {
			if *timeout <= 0 {
				fmt.Fprintln(env.Stderr, "Stopped listening")
			}
			return 0
		}
		if err != nil {
			fmt.Fprintf(env.Stderr, "error: MQTT listen failed: %s\n", agentError(err))
			return 1
		}
		if first {
			fmt.Fprintf(env.Stdout, "Listening on %s (Ctrl+C to stop)\n", res.Topic)
		}
		if !res.Connected && !warned {
			fmt.Fprintf(env.Stderr, "warning: the agent is not connected to the broker right now (%s); waiting\n", orDash(res.LastError))
		}
		warned = !res.Connected
		for _, m := range res.Messages {
			fmt.Fprintf(env.Stdout, "%s %s\n", m.Topic, m.Payload)
		}
		next := res.Next
		after = &next
	}
}

// parseWithArgs parses flags placed before, between or after up to maxArgs
// positional arguments (the Python CLI accepted them anywhere).
func parseWithArgs(fl *flag.FlagSet, args []string, maxArgs int) (rest []string, code int, done bool) {
	for {
		if err := fl.Parse(args); err != nil {
			if errors.Is(err, flag.ErrHelp) {
				return nil, 0, true
			}
			return nil, 2, true
		}
		if fl.NArg() == 0 {
			return rest, 0, false
		}
		rest = append(rest, fl.Arg(0))
		if len(rest) > maxArgs {
			fmt.Fprintf(fl.Output(), "unexpected argument %q\n", fl.Arg(0))
			return nil, 2, true
		}
		args = fl.Args()[1:]
	}
}
