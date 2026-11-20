// Package cli is the meteorcli command line. Each command is one file; the
// table below is the only place that lists them.
package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// Env is what a command may touch. Tests fill it with fakes.
type Env struct {
	Version string
	Paths   config.Paths
	Stdout  io.Writer
	Stderr  io.Writer
	Getenv  func(string) string
	// Owner hands written files to the meteor user when running as root.
	Owner config.Owner
	// ServiceState returns meteorcli.service's state ("active", "not installed", ...).
	ServiceState func(ctx context.Context) string
	// Groups answers group membership for the agent socket; nil means the system database.
	Groups localrpc.Groups
	// AgentUID, when set, replaces the agent's own uid in the socket policy
	// (tests of the group policy run the agent and the CLI as one user).
	AgentUID *uint32
}

func (e *Env) groups() localrpc.Groups {
	if e.Groups != nil {
		return e.Groups
	}
	return localrpc.SystemGroups{}
}

// command is one entry of the command table.
type command struct {
	name    string
	aliases []string
	summary string
	run     func(ctx context.Context, env *Env, args []string) int
}

// commands lists every subcommand. Add a command: one file and one line here.
var commands = []command{
	{name: "agent", aliases: []string{"run"}, summary: "Run the always-on device agent (meteorcli.service runs this).", run: runAgent},
	{name: "claim", summary: "Report (or --wait for) the approval of a pending enrollment request.", run: runClaim},
	{name: "config", summary: "Store the server domain and API key, or import a Python install.", run: runConfig},
	{name: "helper", summary: "Run the root helper (meteorcli-helper.service), or 'helper call' one operation.", run: runHelper},
	{name: "module", summary: "Restart a module of the running agent ('module restart <name>').", run: runModule},
	{name: "register", summary: "Enroll this device with a one-time registration token.", run: runRegister},
	{name: "request-token", aliases: []string{"request"}, summary: "Ask an administrator to approve this device (the agent waits for it).", run: runRequest},
	{name: "status", summary: "Show this device's configuration and enrollment (never secrets).", run: runStatus},
	{name: "test", summary: "Check that the server answers and the API key is valid.", run: runTest},
}

// Main runs the command line and returns the exit code.
func Main(version string, args []string, stdout, stderr io.Writer) int {
	env := &Env{
		Version: version, Stdout: stdout, Stderr: stderr, Getenv: os.Getenv,
		Owner: config.LookupOwner(), ServiceState: systemctlState,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return run(ctx, env, args)
}

func run(ctx context.Context, env *Env, args []string) int {
	fl := flag.NewFlagSet("meteorcli", flag.ContinueOnError)
	fl.SetOutput(env.Stderr)
	showVersion := fl.Bool("version", false, "Print the version and exit.")
	fl.BoolVar(showVersion, "V", false, "Print the version and exit.")
	root := fl.String("root", env.Getenv(config.RootEnv), "Prefix for every path (tests and development; or $"+config.RootEnv+").")
	fl.Usage = func() { usage(env.Stderr) }
	if err := fl.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if *showVersion {
		fmt.Fprintf(env.Stdout, "meteorcli %s\n", env.Version)
		return 0
	}
	env.Paths = config.DefaultPaths(*root)

	rest := fl.Args()
	if len(rest) == 0 {
		usage(env.Stderr)
		return 2
	}
	name := rest[0]
	for _, c := range commands {
		if c.name == name || contains(c.aliases, name) {
			return c.run(ctx, env, rest[1:])
		}
	}
	if name == "help" {
		usage(env.Stdout)
		return 0
	}
	fmt.Fprintf(env.Stderr, "meteorcli: unknown command %q\n\n", name)
	usage(env.Stderr)
	return 2
}

func usage(w io.Writer) {
	fmt.Fprintln(w, "Usage: meteorcli [--root DIR] <command> [flags]")
	fmt.Fprintln(w, "\nMeteorCloud device agent.\n\nCommands:")
	for _, c := range commands {
		name := c.name
		if len(c.aliases) > 0 {
			name += " (" + strings.Join(c.aliases, ", ") + ")"
		}
		fmt.Fprintf(w, "  %-16s %s\n", name, c.summary)
	}
	fmt.Fprintln(w, "\nFlags:\n  -V, --version    Print the version and exit.")
	fmt.Fprintln(w, "\nExamples:\n  sudo meteorcli config --domain meteorxx.com --api-key key_XXXX\n  meteorcli request-token\n  meteorcli register --token reg_XXXX\n  meteorcli status")
}

// newFlags returns a flag set for a subcommand that prints its own usage.
func newFlags(env *Env, name, synopsis string) *flag.FlagSet {
	fl := flag.NewFlagSet("meteorcli "+name, flag.ContinueOnError)
	fl.SetOutput(env.Stderr)
	fl.Usage = func() {
		fmt.Fprintf(env.Stderr, "Usage: meteorcli %s %s\n\n", name, synopsis)
		fl.PrintDefaults()
	}
	return fl
}

// parseFlags parses args and returns the exit code to use when parsing ended the command.
func parseFlags(fl *flag.FlagSet, args []string) (code int, done bool) {
	if err := fl.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0, true
		}
		return 2, true
	}
	if fl.NArg() > 0 {
		fmt.Fprintf(fl.Output(), "unexpected argument %q\n", fl.Arg(0))
		return 2, true
	}
	return 0, false
}

// failWrite explains a write that needed root, like the Python CLI did.
func failWrite(env *Env, err error) int {
	fmt.Fprintf(env.Stderr, "error: %v\n", err)
	if errors.Is(err, fs.ErrPermission) && os.Geteuid() != 0 {
		fmt.Fprintln(env.Stderr, "meteorcli keeps this device's settings in /etc/meteorcli and /var/lib/meteorcli. Run the command with sudo.")
	}
	return 1
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// normalizeSecret strips quotes, spaces and a "Bearer " prefix people paste by accident.
func normalizeSecret(v string) string {
	v = strings.TrimSpace(v)
	v = strings.Trim(v, `"'`)
	v = strings.TrimSpace(v)
	if len(v) >= 7 && strings.EqualFold(v[:7], "bearer ") {
		v = strings.TrimSpace(v[7:])
	}
	return v
}
