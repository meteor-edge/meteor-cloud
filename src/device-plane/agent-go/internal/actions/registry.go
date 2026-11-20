package actions

import (
	"context"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/helper"
)

// caller is the part of the helper client actions use.
type caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// kind is how one action kind runs.
type kind struct {
	// reportFirst sends "done" before running: the device goes down and the
	// answer would be lost (reboot, shutdown).
	reportFirst bool
	// advertised kinds go into capabilities.actions; a stub is not advertised.
	advertised bool
	run        func(ctx context.Context, h caller, a api.Action) error
}

// registry lists every action kind. Add a kind: a function and one line here.
var registry = map[string]kind{
	"reboot":       {reportFirst: true, advertised: true, run: helperOp("power.reboot")},
	"shutdown":     {reportFirst: true, advertised: true, run: helperOp("power.shutdown")},
	"set_password": {advertised: true, run: setPassword},
	"add_user":     {advertised: true, run: addUser},
	"update_agent": {run: updateAgent},
}

// order is the order of Kinds (the Python agent's capabilities order).
var order = []string{"reboot", "shutdown", "set_password", "add_user", "update_agent"}

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

func helperOp(op string) func(context.Context, caller, api.Action) error {
	return func(ctx context.Context, h caller, _ api.Action) error {
		return h.Call(ctx, op, nil, nil)
	}
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func setPassword(ctx context.Context, h caller, a api.Action) error {
	return h.Call(ctx, "users.set_password", helper.SetPasswordRequest{Username: str(a.Username), Password: str(a.Password)}, nil)
}

func addUser(ctx context.Context, h caller, a api.Action) error {
	return h.Call(ctx, "users.add", helper.AddUserRequest{
		Username: str(a.Username), Password: str(a.Password), Sudo: a.Sudo, AuthorizedKey: str(a.AuthorizedKey),
	}, nil)
}

// unsupported is the error of a kind this agent cannot run. The contract wants
// its text to start with "Unsupported" (the Python agent's wording, shown in
// the console as is).
type unsupported string

func (u unsupported) Error() string { return string(u) }

// errUpdateLater is the update_agent stub's answer until the Go updater exists.
const errUpdateLater = unsupported("Unsupported: update_agent is not available in this agent version yet; update with the installer")

func updateAgent(context.Context, caller, api.Action) error { return errUpdateLater }
