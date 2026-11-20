package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"time"
)

// ErrCorrupt means a file existed but could not be parsed. The caller starts clean;
// the bad file has been moved aside (see Quarantine) when that was possible.
var ErrCorrupt = errors.New("file is damaged")

// Object is a JSON object kept as raw values. Reading a file into an Object as well
// as into a struct lets a rewrite keep keys this version of meteorcli does not know.
type Object map[string]json.RawMessage

// WriteFileAtomic replaces path with data so readers see the old or the new file,
// never half of one, even after a power cut: temp file, fsync, rename, fsync dir.
func WriteFileAtomic(path string, data []byte, perm fs.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-")
	if err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	name := tmp.Name()
	ok := false
	defer func() {
		if !ok {
			_ = tmp.Close()
			_ = os.Remove(name)
		}
	}()
	if err := tmp.Chmod(perm); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if _, err := tmp.Write(data); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := tmp.Sync(); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := os.Rename(name, path); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	ok = true
	return syncDir(dir)
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return fmt.Errorf("sync %s: %w", dir, err)
	}
	defer d.Close()
	if err := d.Sync(); err != nil {
		return fmt.Errorf("sync %s: %w", dir, err)
	}
	return nil
}

// ReadJSON reads a JSON object from path into v and also returns it as an Object,
// so WriteJSON can keep unknown keys. A missing file returns an error matching
// fs.ErrNotExist. A file that does not parse returns ErrCorrupt.
func ReadJSON(path string, v any) (Object, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var raw Object
	if err := json.Unmarshal(data, &raw); err != nil || raw == nil {
		return nil, fmt.Errorf("read %s: %w", path, ErrCorrupt)
	}
	if err := json.Unmarshal(data, v); err != nil {
		return nil, fmt.Errorf("read %s: %w: %v", path, ErrCorrupt, err)
	}
	return raw, nil
}

// WriteJSON writes v (a struct that marshals to an object) over the keys in keep and
// saves the result atomically. Structs written this way must not use omitempty:
// a field that is left out would let an old value in keep survive.
func WriteJSON(path string, v any, keep Object, perm fs.FileMode) error {
	data, err := MergeJSON(v, keep)
	if err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return WriteFileAtomic(path, data, perm)
}

// MergeJSON returns keep overlaid with the fields of v, indented for humans.
func MergeJSON(v any, keep Object) ([]byte, error) {
	own, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var fields Object
	if err := json.Unmarshal(own, &fields); err != nil {
		return nil, fmt.Errorf("value is not a JSON object: %w", err)
	}
	out := make(Object, len(keep)+len(fields))
	for k, val := range keep {
		out[k] = val
	}
	for k, val := range fields {
		out[k] = val
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// Quarantine moves a damaged file to <path>.corrupt-<unix time> so a person can
// look at it later, and returns the new name.
func Quarantine(path string, now time.Time) (string, error) {
	dst := path + ".corrupt-" + strconv.FormatInt(now.Unix(), 10)
	if err := os.Rename(path, dst); err != nil {
		return "", fmt.Errorf("quarantine %s: %w", path, err)
	}
	return dst, nil
}

// Owner is who should own the files meteorcli writes as root. When Enabled is
// false (tests, or a process that is not root) files keep the writer's owner.
type Owner struct {
	Enabled bool
	// AgentUID and AgentGID are the meteor system user.
	AgentUID, AgentGID int
}

// LookupOwner finds the meteor user when this process is root. Without it (a dev
// machine, or before the installer ran) ownership is left alone.
func LookupOwner() Owner {
	if os.Geteuid() != 0 {
		return Owner{}
	}
	u, err := user.Lookup("meteor")
	if err != nil {
		return Owner{}
	}
	uid, err1 := strconv.Atoi(u.Uid)
	gid, err2 := strconv.Atoi(u.Gid)
	if err1 != nil || err2 != nil {
		return Owner{}
	}
	return Owner{Enabled: true, AgentUID: uid, AgentGID: gid}
}

// Agent gives path to the meteor user (identity, state, artifacts).
func (o Owner) Agent(path string) error {
	if !o.Enabled {
		return nil
	}
	if err := os.Lchown(path, o.AgentUID, o.AgentGID); err != nil {
		return fmt.Errorf("chown %s: %w", path, err)
	}
	return nil
}

// ConfigFile makes path root:meteor so the agent can read but never change it.
func (o Owner) ConfigFile(path string) error {
	if !o.Enabled {
		return nil
	}
	if err := os.Lchown(path, 0, o.AgentGID); err != nil {
		return fmt.Errorf("chown %s: %w", path, err)
	}
	return nil
}

// EnsureDir creates dir with perm and hands it to the agent user. Missing parents
// are created 0755 so the agent can reach a private directory inside them
// (MkdirAll would otherwise give every new parent the private mode too).
func (o Owner) EnsureDir(dir string, perm fs.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return fmt.Errorf("create %s: %w", filepath.Dir(dir), err)
	}
	if err := os.Mkdir(dir, perm); err != nil && !errors.Is(err, fs.ErrExist) {
		return fmt.Errorf("create %s: %w", dir, err)
	}
	if err := os.Chmod(dir, perm); err != nil {
		return fmt.Errorf("chmod %s: %w", dir, err)
	}
	return o.Agent(dir)
}
