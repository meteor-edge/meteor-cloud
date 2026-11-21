package cli

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
)

// runLogs is `meteorcli logs`: list this device's log sources or print one
// (journal, systemd units, Docker containers, a few /var/log files).
//
//	meteorcli logs [--json]                                 list the sources
//	meteorcli logs SOURCE [-n N] [--since S] [--grep T] [-f] print one (journal, systemd:UNIT, docker:NAME, file:PATH)
//
// The Python helper's syntax works too, because the console's Logs window
// runs it over SSH as /usr/local/libexec/meteorcli-logs (a wrapper around
// this command):
//
//	meteorcli logs list
//	meteorcli logs show KIND [NAME] [--project P] [--lines N] [--since S] [--grep T] [--follow]
//
// Members of the meteorcli group go through the agent, which asks the root
// helper. Root reads the logs itself, with the helper's code and checks, so
// logs work even when the agent is down.
func runLogs(ctx context.Context, env *Env, args []string) int {
	if len(args) > 0 && args[0] == "list" {
		return logsList(ctx, env, args[1:], true)
	}
	if len(args) > 0 && args[0] == "show" {
		return logsShowHelperSyntax(ctx, env, args[1:])
	}
	fl := newFlags(env, "logs", "[SOURCE] [--json] [-n N] [--since 15m|1h|7d] [--grep TEXT] [-f]")
	asJSON := fl.Bool("json", false, "List sources as JSON.")
	lines := fl.Int("lines", 200, "Last N lines.")
	fl.IntVar(lines, "n", 200, "Last N lines (same as --lines).")
	since := fl.String("since", "", "Only newer lines: 15m, 1h, 7d (not for files).")
	grep := fl.String("grep", "", "Only lines containing this text (ignoring case).")
	follow := fl.Bool("follow", false, "Keep printing new lines.")
	fl.BoolVar(follow, "f", false, "Keep printing new lines (same as --follow).")
	positional, code, done := parseInterspersed(fl, args)
	if done {
		return code
	}
	switch len(positional) {
	case 0:
		return logsList(ctx, env, nil, *asJSON)
	case 1:
	default:
		fmt.Fprintf(env.Stderr, "unexpected argument %q\n", positional[1])
		return 2
	}
	kind, name, _ := strings.Cut(positional[0], ":")
	return logsShow(ctx, env, helper.ShowLogsRequest{Kind: kind, Name: name, Lines: *lines, Since: *since, Grep: *grep, Follow: *follow})
}

// parseInterspersed parses flags placed before, between and after the
// positional arguments (Go's flag package stops at the first argument).
func parseInterspersed(fl *flag.FlagSet, args []string) (positional []string, code int, done bool) {
	for {
		if err := fl.Parse(args); err != nil {
			if errors.Is(err, flag.ErrHelp) {
				return nil, 0, true
			}
			return nil, 2, true
		}
		if fl.NArg() == 0 {
			return positional, 0, false
		}
		positional = append(positional, fl.Arg(0))
		args = fl.Args()[1:]
	}
}

func logsShowHelperSyntax(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "logs show", "KIND [NAME] [--project P] [--lines N] [--since S] [--grep T] [--follow]")
	project := fl.String("project", "", "The docker container must belong to this compose project.")
	lines := fl.Int("lines", 200, "Last N lines.")
	since := fl.String("since", "", "Only newer lines: 15m, 1h, 7d.")
	grep := fl.String("grep", "", "Only lines containing this text.")
	follow := fl.Bool("follow", false, "Keep printing new lines.")
	positional, code, done := parseInterspersed(fl, args)
	if done {
		return code
	}
	if len(positional) == 0 || len(positional) > 2 {
		fl.Usage()
		return 2
	}
	r := helper.ShowLogsRequest{Kind: positional[0], Project: *project, Lines: *lines, Since: *since, Grep: *grep, Follow: *follow}
	if len(positional) == 2 {
		r.Name = positional[1]
	}
	return logsShow(ctx, env, r)
}

func logsList(ctx context.Context, env *Env, args []string, asJSON bool) int {
	if len(args) > 0 {
		fmt.Fprintf(env.Stderr, "unexpected argument %q\n", args[0])
		return 2
	}
	var sources []helper.LogSource
	if os.Geteuid() == 0 {
		sources = helper.ListLogs(ctx, helper.SystemHost())
	} else {
		var reply helper.LogsListReply
		if err := localrpc.Call(ctx, env.Paths.AgentSocket(), "logs.list", nil, &reply); err != nil {
			fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
			return 1
		}
		sources = reply.Sources
	}
	if asJSON {
		data, _ := json.Marshal(sources)
		fmt.Fprintln(env.Stdout, string(data))
		return 0
	}
	for _, s := range sources {
		spec := s.Kind
		if s.Kind != "journal" {
			spec += ":" + s.Name
		}
		detail := strings.TrimSpace(s.State + " " + s.Project)
		fmt.Fprintln(env.Stdout, strings.TrimRight(fmt.Sprintf("%-60s %s", spec, detail), " "))
	}
	return 0
}

func logsShow(ctx context.Context, env *Env, r helper.ShowLogsRequest) int {
	if err := r.Validate(); err != nil {
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return 2
	}
	// SSH ends a session with SIGHUP, and a reader that went away gives
	// SIGPIPE: stop cleanly (the tool too) instead of dying and leaving a
	// `journalctl -f` or `tail -F` behind.
	ctx, stop := signal.NotifyContext(ctx, syscall.SIGHUP, syscall.SIGPIPE)
	defer stop()
	if r.Follow {
		ctx = stopOnHangup(ctx)
	}
	out := bufio.NewWriter(env.Stdout)
	var err error
	if os.Geteuid() == 0 {
		err = helper.ShowLogs(ctx, helper.SystemHost(), r, &flushWriter{out})
	} else {
		err = localrpc.CallStream(ctx, env.Paths.AgentSocket(), "logs.show", r, func(line string) error {
			if _, err := out.WriteString(line + "\n"); err != nil {
				return err
			}
			return out.Flush()
		})
	}
	_ = out.Flush()
	switch {
	case err == nil, ctx.Err() != nil:
		return 0
	case localrpc.CodeOf(err) == localrpc.CodeBadRequest:
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return 2
	default:
		fmt.Fprintf(env.Stderr, "error: %s\n", agentError(err))
		return 1
	}
}

// flushWriter flushes after every write, so followed lines show at once.
type flushWriter struct{ w *bufio.Writer }

func (f *flushWriter) Write(p []byte) (int, error) {
	n, err := f.w.Write(p)
	if err == nil {
		err = f.w.Flush()
	}
	return n, err
}

// stopOnHangup ends ctx when stdin, a pipe or socket (SSH, sudo), closes: a
// followed log can stay quiet for hours, and the console's Logs window ends
// by closing the SSH session. A terminal ends with Ctrl+C instead.
func stopOnHangup(ctx context.Context) context.Context {
	st, err := os.Stdin.Stat()
	if err != nil || st.Mode()&(os.ModeNamedPipe|os.ModeSocket) == 0 {
		return ctx
	}
	ctx, cancel := context.WithCancel(ctx)
	go func() { // owned: ends with stdin, and the process ends with the command
		_, _ = io.Copy(io.Discard, os.Stdin)
		cancel()
	}()
	return ctx
}
