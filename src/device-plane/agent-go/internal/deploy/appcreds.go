package deploy

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Application credentials for workloads bound to a MeteorCloud application
// (spec.meteorcloud.application). The agent gets a device-scoped token from the
// control plane, keeps it in a file mounted read-only into the container at
// /run/secrets/meteorcloud-token, and sets the METEORCLOUD_* variables the SDK
// reads. The token is never in an image, a compose file, the container
// environment or a log line. It is rewritten in place before it expires, so the
// running container sees the new token without a restart.

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9_.-]`)

// credErr is a credentials problem reported as the workload's last_error.
type credErr struct{ msg string }

func (e credErr) Error() string { return e.msg }

// binding returns the item's application binding, nil when it has none.
func binding(it item) map[string]any {
	b, _ := it.spec()["meteorcloud"].(map[string]any)
	if b == nil || !runtime.Spec(b).Bool("application", false) {
		return nil
	}
	return b
}

// credMeta is a workload's meta.json.
type credMeta struct {
	Application  any               `json:"application"`
	Environment  any               `json:"environment"`
	CredentialID any               `json:"credential_id"`
	ExpiresAt    string            `json:"expires_at"`
	RefreshAfter string            `json:"refresh_after"`
	Env          map[string]string `json:"env"`
}

// credStore keeps token files and their metadata, one directory per workload.
type credStore struct {
	root string
	now  func() time.Time
}

// dir is a readable prefix plus a digest of the full key, so two keys that
// differ only in unsafe characters never share a directory.
func (s credStore) dir(key string) string {
	prefix := strings.Trim(unsafeName.ReplaceAllString(key, "_"), ".")
	if len(prefix) > 48 {
		prefix = prefix[:48]
	}
	if prefix == "" {
		prefix = "_"
	}
	sum := sha256.Sum256([]byte(key))
	return filepath.Join(s.root, prefix+"-"+hex.EncodeToString(sum[:])[:12])
}

func (s credStore) meta(key string) (credMeta, bool) {
	var m credMeta
	data, err := os.ReadFile(filepath.Join(s.dir(key), "meta.json"))
	if err != nil || json.Unmarshal(data, &m) != nil {
		return credMeta{}, false
	}
	return m, true
}

// current is the stored credential when it exists and still belongs to b.
func (s credStore) current(key string, b map[string]any) *runtime.App {
	m, ok := s.meta(key)
	token := filepath.Join(s.dir(key), "token")
	st, err := os.Stat(token)
	if !ok || err != nil || st.Size() == 0 {
		return nil
	}
	env := b["environment"]
	if env == nil || env == "" {
		env = "production"
	}
	if m.Application != b["application"] || m.Environment != env {
		return nil
	}
	return &runtime.App{TokenFile: token, Env: m.Env}
}

func parseTime(s string) (time.Time, bool) {
	s = strings.Replace(s, "Z", "+00:00", 1)
	for _, layout := range []string{"2006-01-02T15:04:05.999999-07:00", "2006-01-02T15:04:05.999999"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// due reports whether the meta's time field has passed (or is unknown).
func (s credStore) due(key string, field func(credMeta) string) bool {
	m, _ := s.meta(key)
	t, ok := parseTime(field(m))
	return !ok || !s.now().Before(t)
}

func (s credStore) refreshDue(key string) bool {
	return s.due(key, func(m credMeta) string { return m.RefreshAfter })
}

func (s credStore) expired(key string) bool {
	return s.due(key, func(m credMeta) string { return m.ExpiresAt })
}

// mint asks the server for a new token and stores it.
func (s credStore) mint(ctx context.Context, client *api.Client, token, key string) (*runtime.App, error) {
	issued, err := client.AppCredentials(ctx, token, key)
	if err != nil {
		if api.IsAuth(err) {
			return nil, err
		}
		return nil, credErr{"Could not obtain application credentials: " + message(err)}
	}
	if issued.Token == "" {
		return nil, credErr{"The control plane answered without an application token."}
	}
	env := map[string]string{
		"METEORCLOUD_TOKEN_FILE":      runtime.TokenMountPath,
		"METEORCLOUD_APP_ID":          issued.Application,
		"METEORCLOUD_DEVICE_ID":       issued.DeviceID,
		"METEORCLOUD_ENVIRONMENT":     issued.Environment,
		"METEORCLOUD_ORGANIZATION_ID": issued.OrganizationID,
	}
	if issued.Endpoint != nil && *issued.Endpoint != "" {
		env["METEORCLOUD_ENDPOINT"] = *issued.Endpoint
	}
	dir := s.dir(key)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "token")
	if err := writeInPlace(path, []byte(issued.Token+"\n")); err != nil {
		return nil, err
	}
	meta := credMeta{
		Application: issued.Application, Environment: issued.Environment, CredentialID: issued.CredentialID,
		ExpiresAt: issued.ExpiresAt, RefreshAfter: issued.RefreshAfter, Env: env,
	}
	data, _ := json.MarshalIndent(meta, "", "  ")
	if err := config.WriteFileAtomic(filepath.Join(dir, "meta.json"), append(data, '\n'), 0o600); err != nil {
		return nil, err
	}
	return &runtime.App{TokenFile: path, Env: env}, nil
}

// writeInPlace replaces a file's content without replacing the file: a
// single-file bind mount follows the inode, so a rename would leave the
// container with the old token. The new token is written over the old one in
// one write and the file cut afterwards, so a reader never sees it empty.
// The file is world-readable for containers that do not run as root; its
// directory is the agent's own (0700).
func writeInPlace(path string, data []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE, 0o644)
	if err != nil {
		return err
	}
	n, err := f.WriteAt(data, 0)
	if err == nil && n != len(data) {
		err = fmt.Errorf("short write to %s", path)
	}
	if err == nil {
		err = f.Truncate(int64(len(data)))
	}
	if err == nil {
		err = f.Sync()
	}
	err = errors.Join(err, f.Chmod(0o644), f.Close())
	return err
}

// forApply returns the credential to start a workload with: a valid stored
// one, otherwise a new one (the stored one while offline, until it expires).
func (s credStore) forApply(ctx context.Context, client *api.Client, token string, it item) (*runtime.App, error) {
	b := binding(it)
	if b == nil {
		return nil, nil
	}
	cur := s.current(it.key(), b)
	if cur != nil && !s.refreshDue(it.key()) {
		return cur, nil
	}
	app, err := s.mint(ctx, client, token, it.key())
	if err != nil && cur != nil && !s.expired(it.key()) && !api.IsAuth(err) {
		return cur, nil // offline: the stored token still works
	}
	return app, err
}

// keepFresh rotates a running workload's token when due. It returns false
// when the workload must be applied again (token lost or binding changed).
func (s credStore) keepFresh(ctx context.Context, client *api.Client, token string, it item, log func(string, ...any)) bool {
	b := binding(it)
	if b == nil {
		return true
	}
	if s.current(it.key(), b) == nil {
		return false
	}
	if s.refreshDue(it.key()) {
		if _, err := s.mint(ctx, client, token, it.key()); err != nil {
			// A full disk or an offline server must not stop the other workloads.
			log("application token was not rotated", "workload", it.key(), "err", err)
		}
	}
	return true
}

// prune deletes stored tokens of workloads no longer desired or no longer bound.
func (s credStore) prune(keep map[string]bool) {
	entries, err := os.ReadDir(s.root)
	if err != nil {
		return
	}
	wanted := map[string]bool{}
	for k := range keep {
		wanted[filepath.Base(s.dir(k))] = true
	}
	for _, e := range entries {
		if e.IsDir() && !wanted[e.Name()] {
			_ = os.RemoveAll(filepath.Join(s.root, e.Name()))
		}
	}
}
