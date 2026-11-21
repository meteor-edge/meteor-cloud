// Package credstore keeps the application credentials of workloads bound to a
// MeteorCloud application (spec.meteorcloud.application): a token file mounted
// read-only into the container at /run/secrets/meteorcloud-token, and the
// METEORCLOUD_* variables the SDK reads. The token is never in an image, a
// compose file, the container environment or a log line.
//
// It is a library, not a module: deploy obtains a credential before it starts
// a workload, and the appcreds module rotates stored tokens before they
// expire. Both go through a Store, which serializes its writers with an flock,
// so they never interleave.
package credstore

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
)

// TokenMountPath is where a bound container finds its application token.
const TokenMountPath = "/run/secrets/meteorcloud-token"

// metaVersion is the meta.json format.
const metaVersion = 1

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9_.-]`)

// Binding is the application (and environment) a workload is bound to. An
// empty environment means production.
type Binding struct {
	Application string
	Environment string
}

func (b Binding) env() string {
	if b.Environment == "" {
		return "production"
	}
	return b.Environment
}

// Credential is a stored token: the file to mount and the variables to set.
type Credential struct {
	TokenFile string
	Env       map[string]string
}

// Error is a credentials problem reported as the workload's last_error.
type Error struct{ Msg string }

func (e Error) Error() string { return e.Msg }

// Meta is a workload's meta.json.
type Meta struct {
	Version      int               `json:"version"`
	WorkloadKey  string            `json:"workload_key"`
	Application  string            `json:"application"`
	Environment  string            `json:"environment"`
	CredentialID string            `json:"credential_id"`
	ExpiresAt    string            `json:"expires_at"`
	RefreshAfter string            `json:"refresh_after"`
	Env          map[string]string `json:"env"`
}

// Store keeps token files and their metadata, one directory per workload.
type Store struct {
	Root string
	// Now is the clock (tests); nil means time.Now.
	Now func() time.Time
}

func (s Store) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// Dir is a readable prefix plus a digest of the full key, so two keys that
// differ only in unsafe characters never share a directory.
func (s Store) Dir(key string) string {
	prefix := strings.Trim(unsafeName.ReplaceAllString(key, "_"), ".")
	if len(prefix) > 48 {
		prefix = prefix[:48]
	}
	if prefix == "" {
		prefix = "_"
	}
	sum := sha256.Sum256([]byte(key))
	return filepath.Join(s.Root, prefix+"-"+hex.EncodeToString(sum[:])[:12])
}

// Meta reads a workload's meta.json.
func (s Store) Meta(key string) (Meta, bool) {
	return readMeta(filepath.Join(s.Dir(key), "meta.json"))
}

func readMeta(path string) (Meta, bool) {
	var m Meta
	data, err := os.ReadFile(path)
	if err != nil || json.Unmarshal(data, &m) != nil {
		return Meta{}, false
	}
	return m, true
}

// Current is the stored credential when it exists and still belongs to b.
func (s Store) Current(key string, b Binding) *Credential {
	m, ok := s.Meta(key)
	token := filepath.Join(s.Dir(key), "token")
	st, err := os.Stat(token)
	if !ok || err != nil || st.Size() == 0 || m.Application != b.Application || m.Environment != b.env() {
		return nil
	}
	return &Credential{TokenFile: token, Env: m.Env}
}

// parseTime reads the server's ISO 8601 times (with or without an offset).
func parseTime(v string) (time.Time, bool) {
	v = strings.Replace(v, "Z", "+00:00", 1)
	for _, layout := range []string{"2006-01-02T15:04:05.999999-07:00", "2006-01-02T15:04:05.999999"} {
		if t, err := time.Parse(layout, v); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// passed reports whether a meta time has passed (or is unknown).
func (s Store) passed(v string) bool {
	t, ok := parseTime(v)
	return !ok || !s.now().Before(t)
}

// RefreshDue reports that the stored token should be replaced.
func (s Store) RefreshDue(key string) bool {
	m, _ := s.Meta(key)
	return s.passed(m.RefreshAfter)
}

// Expired reports that the stored token no longer works.
func (s Store) Expired(key string) bool {
	m, _ := s.Meta(key)
	return s.passed(m.ExpiresAt)
}

// lock holds the store's flock until the returned function is called.
func (s Store) lock() (func(), error) {
	if err := os.MkdirAll(s.Root, 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(s.Root, ".lock"), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		f.Close()
		return nil, err
	}
	return func() { f.Close() }, nil
}

// Mint asks the server for a new token for b and stores it. The server issues
// it for the binding of the workload's desired release; one for another
// application or environment is refused and the stored token is left alone.
// With mustExist, nothing is written unless the workload already has a
// credential (a rotation must not bring back one that deploy just removed).
func (s Store) Mint(ctx context.Context, client *api.Client, token, key string, b Binding, mustExist bool) (*Credential, error) {
	issued, err := client.AppCredentials(ctx, token, key)
	if err != nil {
		if api.IsAuth(err) {
			return nil, err
		}
		return nil, Error{"Could not obtain application credentials: " + message(err)}
	}
	if issued.Token == "" {
		return nil, Error{"The control plane answered without an application token."}
	}
	if issued.Application != b.Application || issued.Environment != b.env() {
		return nil, Error{fmt.Sprintf("The control plane issued credentials for application %s (%s), not for %s; "+
			"this release's binding is not the desired release's.", issued.Application, issued.Environment, b.Application)}
	}
	env := map[string]string{
		"METEORCLOUD_TOKEN_FILE":      TokenMountPath,
		"METEORCLOUD_APP_ID":          issued.Application,
		"METEORCLOUD_DEVICE_ID":       issued.DeviceID,
		"METEORCLOUD_ENVIRONMENT":     issued.Environment,
		"METEORCLOUD_ORGANIZATION_ID": issued.OrganizationID,
	}
	if issued.Endpoint != nil && *issued.Endpoint != "" {
		env["METEORCLOUD_ENDPOINT"] = *issued.Endpoint
	}
	unlock, err := s.lock()
	if err != nil {
		return nil, err
	}
	defer unlock()
	dir := s.Dir(key)
	if _, err := os.Stat(filepath.Join(dir, "meta.json")); mustExist && err != nil {
		return nil, fs.ErrNotExist
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "token")
	if err := writeInPlace(path, []byte(issued.Token+"\n")); err != nil {
		return nil, err
	}
	meta := Meta{Version: metaVersion, WorkloadKey: key, Application: issued.Application, Environment: issued.Environment,
		CredentialID: issued.CredentialID, ExpiresAt: issued.ExpiresAt, RefreshAfter: issued.RefreshAfter, Env: env}
	data, _ := json.MarshalIndent(meta, "", "  ")
	if err := config.WriteFileAtomic(filepath.Join(dir, "meta.json"), append(data, '\n'), 0o600); err != nil {
		return nil, err
	}
	return &Credential{TokenFile: path, Env: env}, nil
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
	return errors.Join(err, f.Chmod(0o644), f.Close())
}

// Prune deletes stored tokens of workloads not in keep.
func (s Store) Prune(keep map[string]bool) {
	unlock, err := s.lock()
	if err != nil {
		return
	}
	defer unlock()
	entries, err := os.ReadDir(s.Root)
	if err != nil {
		return
	}
	wanted := map[string]bool{}
	for k := range keep {
		wanted[filepath.Base(s.Dir(k))] = true
	}
	for _, e := range entries {
		if e.IsDir() && !wanted[e.Name()] {
			_ = os.RemoveAll(filepath.Join(s.Root, e.Name()))
		}
	}
}

// All returns the meta of every stored credential that names its workload.
func (s Store) All() []Meta {
	entries, err := os.ReadDir(s.Root)
	if err != nil {
		return nil
	}
	var out []Meta
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if m, ok := readMeta(filepath.Join(s.Root, e.Name(), "meta.json")); ok && m.WorkloadKey != "" {
			out = append(out, m)
		}
	}
	return out
}

func message(err error) string {
	var e *api.Error
	if errors.As(err, &e) {
		return e.Message
	}
	return err.Error()
}
