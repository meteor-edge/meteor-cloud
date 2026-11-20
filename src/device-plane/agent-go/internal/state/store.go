// Package state keeps what modules must remember across restarts: small versioned
// JSON files and the outbox of reports waiting for the server.
package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

// ErrCorrupt means a state file did not parse. It has been moved to
// <name>.corrupt-<unix time> and the module should start clean.
var ErrCorrupt = config.ErrCorrupt

// validName keeps state file names flat and boring: no paths, no dot files.
var validName = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,62}$`)

// Store reads and writes state files in one directory (meteor 0700). Each file is
// a JSON object with a "version" field; keys the writer does not know are kept.
type Store struct {
	dir string
	now func() time.Time
}

// NewStore opens dir, creating it with mode 0700 when missing.
func NewStore(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("state: %w", err)
	}
	return &Store{dir: dir, now: time.Now}, nil
}

// Dir is the directory the store writes to.
func (s *Store) Dir() string { return s.dir }

// Read loads name into v and returns the version stored in the file (0 for files
// written before versions existed, such as ones imported from the Python agent).
// A missing file returns an error matching fs.ErrNotExist; a damaged one is moved
// aside and returns ErrCorrupt.
func (s *Store) Read(name string, v any) (int, error) {
	path, err := s.path(name)
	if err != nil {
		return 0, err
	}
	var head struct {
		Version json.Number `json:"version"`
	}
	raw, err := config.ReadJSON(path, &head)
	if errors.Is(err, config.ErrCorrupt) {
		return 0, s.quarantine(path, err)
	}
	if err != nil {
		return 0, fmt.Errorf("state %s: %w", name, err)
	}
	version := 0
	if head.Version != "" {
		n, err := head.Version.Int64()
		if err != nil || n < 0 {
			return 0, s.quarantine(path, fmt.Errorf("bad version %q: %w", head.Version, config.ErrCorrupt))
		}
		version = int(n)
	}
	data, _ := json.Marshal(raw)
	if err := json.Unmarshal(data, v); err != nil {
		return 0, s.quarantine(path, fmt.Errorf("%w: %v", config.ErrCorrupt, err))
	}
	return version, nil
}

// Write saves v (a struct without omitempty fields, see config.WriteJSON) as name
// with the given version, keeping unknown keys already in the file.
func (s *Store) Write(name string, version int, v any) error {
	path, err := s.path(name)
	if err != nil {
		return err
	}
	var keep config.Object
	_, err = config.ReadJSON(path, &keep)
	switch {
	case err == nil:
	case errors.Is(err, fs.ErrNotExist), errors.Is(err, config.ErrCorrupt):
		keep = nil // nothing worth keeping
	default:
		// Unreadable (permissions, I/O): do not replace what we could not read.
		return fmt.Errorf("state %s: %w", name, err)
	}
	merged, err := config.MergeJSON(v, keep)
	if err != nil {
		return fmt.Errorf("state %s: %w", name, err)
	}
	var out config.Object
	if err := json.Unmarshal(merged, &out); err != nil {
		return fmt.Errorf("state %s: %w", name, err)
	}
	// The version argument wins over any "version" field in v.
	out["version"] = json.RawMessage(fmt.Sprint(version))
	if err := config.WriteJSON(path, struct{}{}, out, 0o600); err != nil {
		return fmt.Errorf("state %s: %w", name, err)
	}
	return nil
}

// Remove deletes name. A missing file is not an error.
func (s *Store) Remove(name string) error {
	path, err := s.path(name)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("state %s: %w", name, err)
	}
	return nil
}

func (s *Store) path(name string) (string, error) {
	if !validName.MatchString(name) {
		return "", fmt.Errorf("state: invalid file name %q", name)
	}
	return filepath.Join(s.dir, name), nil
}

func (s *Store) quarantine(path string, cause error) error {
	dst, err := config.Quarantine(path, s.now())
	if err != nil {
		return fmt.Errorf("state %s: %w (and %v)", filepath.Base(path), cause, err)
	}
	return fmt.Errorf("state %s: %w (moved to %s)", filepath.Base(path), cause, filepath.Base(dst))
}
