package actions

import (
	"context"
	"fmt"
	"regexp"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/state"
)

// caller is the part of the helper client actions use.
type caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// env is what an action kind may use.
type env struct {
	// d is for the workload_* kinds (state, lock, signals, API).
	d      agent.Deps
	helper caller
	// state keeps what must survive the restart an action causes (update_agent).
	state   *state.Store
	version string
	now     func() time.Time
	// restart asks the supervisor to restart (unpark) a module (restart_module).
	restart func(name string) error
	// diagnose returns the doctor report as text (run_diagnostics).
	diagnose func(ctx context.Context) string
}

// kind is how one action kind runs.
type kind struct {
	// reportFirst sends "done" before running: the device goes down and the
	// answer would be lost (reboot, shutdown).
	reportFirst bool
	// advertised kinds go into capabilities.actions; a stub is not advertised.
	advertised bool
	// later kinds report when they have finished, not when run returns
	// (update_agent: the updater restarts the agent and writes an outcome).
	later bool
	run   func(ctx context.Context, e env, a api.Action) error
	// output, when set, runs instead of run and its text goes into the
	// result's output (run_diagnostics).
	output func(ctx context.Context, e env, a api.Action) (string, error)
}

// registry lists every action kind. Add a kind: a function and one line here.
var registry = map[string]kind{
	"reboot":       {reportFirst: true, advertised: true, run: helperOp("power.reboot")},
	"shutdown":     {reportFirst: true, advertised: true, run: helperOp("power.shutdown")},
	"set_password": {advertised: true, run: setPassword},
	"add_user":     {advertised: true, run: addUser},
	"update_agent": {advertised: true, later: true, run: startUpdate},
	// Remote repair from the console's module health panel.
	"restart_module":  {advertised: true, run: restartModule},
	"run_diagnostics": {advertised: true, output: runDiagnostics},
	// One deployed workload, from the console's software page (workload.go).
	"workload_restart":  {advertised: true, run: controlWorkload("restart")},
	"workload_stop":     {advertised: true, run: controlWorkload("stop")},
	"workload_start":    {advertised: true, run: controlWorkload("start")},
	"workload_snapshot": {advertised: true, run: snapshotWorkload},
}

// order is the order of Kinds (the Python agent's capabilities order, then
// the kinds only this agent has).
var order = []string{"reboot", "shutdown", "set_password", "add_user", "update_agent", "restart_module", "run_diagnostics",
	"workload_restart", "workload_stop", "workload_start", "workload_snapshot"}

// Kinds returns the action kinds this agent can run, for capabilities.actions.
func Kinds() []string {
	var out []string
	for _, k := range order {
		if registry[k].advertised {
			out = append(out, k)
		}
	}
	return out
}

func helperOp(op string) func(context.Context, env, api.Action) error {
	return func(ctx context.Context, e env, _ api.Action) error {
		return e.helper.Call(ctx, op, nil, nil)
	}
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func setPassword(ctx context.Context, e env, a api.Action) error {
	return e.helper.Call(ctx, "users.set_password", helper.SetPasswordRequest{Username: str(a.Username), Password: str(a.Password)}, nil)
}

func addUser(ctx context.Context, e env, a api.Action) error {
	return e.helper.Call(ctx, "users.add", helper.AddUserRequest{
		Username: str(a.Username), Password: str(a.Password), Sudo: a.Sudo, AuthorizedKey: str(a.AuthorizedKey),
	}, nil)
}

// moduleName is what a module name may look like (the server checks the same).
var moduleName = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)

// restartModule restarts one module through the supervisor; a parked module
// runs again. The restart is only queued here: the runner asks the supervisor
// once the result is in the outbox, so restarting the actions module itself
// cannot lose the report.
func restartModule(_ context.Context, e env, a api.Action) error {
	name := str(a.Module)
	if !moduleName.MatchString(name) {
		return fmt.Errorf("restart_module: invalid module name %q", name)
	}
	return e.restart(name)
}

// runDiagnostics returns the doctor report; checks that fail are part of the
// report, not a failure of the action.
func runDiagnostics(ctx context.Context, e env, _ api.Action) (string, error) {
	return e.diagnose(ctx), nil
}

// unsupported is the error of a kind this agent cannot run. The contract wants
// its text to start with "Unsupported" (the Python agent's wording, shown in
// the console as is).
type unsupported string

func (u unsupported) Error() string { return string(u) }
