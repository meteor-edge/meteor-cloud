package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

// reportVersion is the format of one outbox file.
const reportVersion = 1

// DefaultOutboxLimit bounds the outbox so a device offline for weeks cannot fill
// its disk. The oldest reports go first.
const DefaultOutboxLimit = 500

// Report is one message waiting for the server, such as a deploy result.
type Report struct {
	Version int    `json:"version"`
	ID      string `json:"id"`
	// Kind says which route sends it ("software", "action", ...).
	Kind string `json:"kind"`
	// Key, when set, means only the latest report with this Kind and Key matters.
	Key     string          `json:"key"`
	Created time.Time       `json:"created"`
	Body    json.RawMessage `json:"body"`
}

// Outbox stores reports on disk until the server has accepted them, so a result
// is never lost to a reboot or a network outage. One file per report keeps every
// write atomic and small. Modules Put; whoever sends (the heartbeat) reads
// Pending and calls Ack only after the server said yes.
type Outbox struct {
	dir   string
	limit int
	now   func() time.Time

	mu      sync.Mutex
	seq     uint64
	dropped int
}

// NewOutbox opens the outbox in dir (created 0700). limit <= 0 means DefaultOutboxLimit.
func NewOutbox(dir string, limit int) (*Outbox, error) {
	if limit <= 0 {
		limit = DefaultOutboxLimit
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("outbox: %w", err)
	}
	return &Outbox{dir: dir, limit: limit, now: time.Now}, nil
}

// Put stores a report. With a non-empty key, earlier reports with the same kind
// and key are replaced, because the server only needs the latest one.
func (o *Outbox) Put(kind, key string, body any) (Report, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return Report{}, fmt.Errorf("outbox %s: %w", kind, err)
	}
	o.mu.Lock()
	defer o.mu.Unlock()

	o.seq++
	now := o.now().UTC()
	r := Report{
		Version: reportVersion,
		ID:      fmt.Sprintf("%020d-%06d", now.UnixNano(), o.seq%1_000_000),
		Kind:    kind, Key: key, Created: now, Body: data,
	}
	file, err := json.Marshal(r)
	if err != nil {
		return Report{}, fmt.Errorf("outbox %s: %w", kind, err)
	}
	if err := config.WriteFileAtomic(o.file(r.ID), file, 0o600); err != nil {
		return Report{}, fmt.Errorf("outbox %s: %w", kind, err)
	}

	all, err := o.load()
	if err != nil {
		return r, err
	}
	var keep []Report
	for _, old := range all {
		if key != "" && old.ID != r.ID && old.Kind == kind && old.Key == key {
			if err := o.remove(old.ID); err != nil {
				return r, err
			}
			continue
		}
		keep = append(keep, old)
	}
	for len(keep) > o.limit {
		if err := o.remove(keep[0].ID); err != nil {
			return r, err
		}
		keep = keep[1:]
		o.dropped++
	}
	return r, nil
}

// Pending returns reports of kind ("" for all), oldest first. Damaged files are
// moved aside rather than blocking the reports behind them.
func (o *Outbox) Pending(kind string) ([]Report, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	all, err := o.load()
	if err != nil || kind == "" {
		return all, err
	}
	var out []Report
	for _, r := range all {
		if r.Kind == kind {
			out = append(out, r)
		}
	}
	return out, nil
}

// Ack removes reports the server accepted. Unknown ids are ignored, so a retry
// after a crash is harmless.
func (o *Outbox) Ack(ids ...string) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	for _, id := range ids {
		if err := o.remove(id); err != nil {
			return err
		}
	}
	return nil
}

// Dropped counts reports thrown away because the outbox was full.
func (o *Outbox) Dropped() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.dropped
}

func (o *Outbox) load() ([]Report, error) {
	entries, err := os.ReadDir(o.dir)
	if err != nil {
		return nil, fmt.Errorf("outbox: %w", err)
	}
	var out []Report
	for _, e := range entries {
		name := e.Name()
		if !e.Type().IsRegular() || !strings.HasSuffix(name, ".json") || strings.HasPrefix(name, ".") {
			continue
		}
		path := filepath.Join(o.dir, name)
		data, err := os.ReadFile(path)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("outbox: %w", err)
		}
		var r Report
		if json.Unmarshal(data, &r) != nil || r.ID+".json" != name {
			if _, qerr := config.Quarantine(path, o.now()); qerr != nil {
				return nil, fmt.Errorf("outbox: %w", qerr)
			}
			continue
		}
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

func (o *Outbox) remove(id string) error {
	if strings.ContainsAny(id, "/\\") || strings.HasPrefix(id, ".") {
		return fmt.Errorf("outbox: invalid id %q", id)
	}
	if err := os.Remove(o.file(id)); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("outbox: %w", err)
	}
	return nil
}

func (o *Outbox) file(id string) string { return filepath.Join(o.dir, id+".json") }
