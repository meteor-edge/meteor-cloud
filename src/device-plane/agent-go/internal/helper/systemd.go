package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// maxUnitBytes matches the Python meteorcli-systemd helper.
const maxUnitBytes = 256 << 10

// InstallUnitRequest is the params of systemd.install_unit. A systemd deploy is
// root-equivalent by design (as Docker is): the helper checks the name and the
// shape of the file, not what ExecStart runs. The agent only asks for units
// from a verified release.
type InstallUnitRequest struct {
	// Name is the unit file name, e.g. "demo-app.service".
	Name string `json:"name"`
	// Content is the unit file.
	Content string `json:"content"`
	// NoStart installs and enables without (re)starting.
	NoStart bool `json:"no_start"`
}

// InstallUnitReply is the answer of systemd.install_unit.
type InstallUnitReply struct {
	Installed string `json:"installed"`
	Started   bool   `json:"started"`
}

func (r *InstallUnitRequest) validate() error {
	bad := func(format string, args ...any) error {
		return localrpc.Errorf(localrpc.CodeBadRequest, format, args...)
	}
	if !validName.MatchString(r.Name) || !strings.HasSuffix(r.Name, ".service") || r.Name == ".service" {
		return bad("name must be a .service file name of a-z, 0-9, '.', '_' or '-'")
	}
	if strings.HasPrefix(r.Name, "meteorcli") {
		return bad("meteorcli units cannot be changed through the helper")
	}
	switch {
	case len(r.Content) > maxUnitBytes:
		return bad("unit file is larger than %d bytes", maxUnitBytes)
	case !utf8.ValidString(r.Content) || strings.ContainsRune(r.Content, 0):
		return bad("unit file must be UTF-8 text")
	case !strings.Contains(r.Content, "[Service]"):
		return bad("unit file must contain a [Service] section")
	}
	return nil
}

// auditInstallUnit logs the unit name; the content may hold secrets.
func auditInstallUnit(raw json.RawMessage) []any {
	var r InstallUnitRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"unit", r.Name, "no_start", r.NoStart}
}

func (h Host) installUnit(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var req InstallUnitRequest
	if err := decode(raw, &req); err != nil {
		return nil, err
	}
	if err := req.validate(); err != nil {
		return nil, err
	}
	dest := filepath.Join(h.UnitDir, req.Name)
	if st, err := os.Lstat(dest); err == nil && st.Mode()&fs.ModeSymlink != 0 {
		return nil, localrpc.Errorf(localrpc.CodeBadRequest, "%s is a symlink", dest)
	} else if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	if err := os.MkdirAll(h.UnitDir, 0o755); err != nil {
		return nil, err
	}
	// The temp file does not end in .service, so systemd ignores a leftover.
	if err := config.WriteFileAtomic(dest, bytes.Clone([]byte(req.Content)), 0o644); err != nil {
		return nil, err
	}
	steps := [][]string{{"daemon-reload"}, {"enable", "--", req.Name}}
	if !req.NoStart {
		steps = append(steps, []string{"restart", "--", req.Name})
	}
	for _, args := range steps {
		if _, err := h.Run(ctx, "systemctl", args...); err != nil {
			return nil, err
		}
	}
	return InstallUnitReply{Installed: req.Name, Started: !req.NoStart}, nil
}
