package helper

import (
	"context"
	"encoding/json"
	"regexp"

	"meteorcloud/meteorcli/internal/localrpc"
)

// UpdaterUnit is the transient unit `meteorcli updater` runs in. One fixed
// name: systemd itself refuses a second update while one runs.
const UpdaterUnit = "meteorcli-update"

// defaultBin is the installed meteorcli the updater runs from and replaces.
const defaultBin = "/usr/local/bin/meteorcli"

// validActionID matches the control plane's action ids (UUIDs) without
// allowing anything that could be read as an option.
var validActionID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9-]{0,63}$`)

// UpdateStartRequest is the params of update.start.
type UpdateStartRequest struct {
	// ActionID is the update_agent action the outcome belongs to ("" when an
	// administrator started the update from the command line).
	ActionID string `json:"action_id"`
}

// UpdateStartReply is the answer of update.start: the unit to follow.
type UpdateStartReply struct {
	Unit string `json:"unit"`
}

// updaterProperties sandbox the updater: it writes only the binary's
// directory, the agent's state (the outcome file) and its own lock directory.
var updaterProperties = []string{
	"Description=meteorcli updater",
	"RuntimeMaxSec=30min",
	"RuntimeDirectory=meteorcli-update",
	"ProtectSystem=strict",
	"ReadWritePaths=/usr/local/bin /var/lib/meteorcli",
	"ProtectHome=yes",
	"PrivateTmp=yes",
	"NoNewPrivileges=yes",
	"ProtectKernelTunables=yes",
	"ProtectKernelModules=yes",
	"ProtectKernelLogs=yes",
	"ProtectControlGroups=yes",
	"RestrictSUIDSGID=yes",
	"RestrictRealtime=yes",
	"LockPersonality=yes",
}

func auditUpdate(raw json.RawMessage) []any {
	var r UpdateStartRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"action_id", r.ActionID}
}

// updateStart starts `meteorcli updater` as its own transient unit and returns
// at once: the updater restarts the agent (and stops this helper), so it must
// not run inside either of their units.
func (h Host) updateStart(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var req UpdateStartRequest
	if err := decode(raw, &req); err != nil {
		return nil, err
	}
	if req.ActionID != "" && !validActionID.MatchString(req.ActionID) {
		return nil, localrpc.Errorf(localrpc.CodeBadRequest, "action_id must be letters, digits and '-'")
	}
	bin := h.UpdaterBin
	if bin == "" {
		bin = defaultBin
	}
	args := []string{"--unit=" + UpdaterUnit, "--collect", "--no-block", "--quiet"}
	for _, p := range updaterProperties {
		args = append(args, "--property="+p)
	}
	args = append(args, "--", bin, "updater")
	if req.ActionID != "" {
		args = append(args, "--action", req.ActionID)
	}
	if _, err := h.Run(ctx, "systemd-run", args...); err != nil {
		return nil, err
	}
	return UpdateStartReply{Unit: UpdaterUnit + ".service"}, nil
}
