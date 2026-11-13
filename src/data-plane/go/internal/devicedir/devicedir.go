// Package devicedir knows which organization owns each enrolled device.
//
// Device ingestion binds the device id in an MQTT topic to an organization with
// it, so identity never comes from a payload. The control plane is the source of
// truth (GET /internal/data-plane/devices); ingestion keeps the last snapshot and
// serves from it while the control plane is unreachable, up to a maximum staleness
// after which it fails closed.
package devicedir

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// ErrStale means no snapshot is loaded, or the last one is older than allowed.
var ErrStale = errors.New("device directory is unavailable or too old")

// Snapshot mirrors the control-plane response.
type Snapshot struct {
	Version     string    `json:"version"`
	GeneratedAt time.Time `json:"generated_at"`
	Devices     []Entry   `json:"devices"`
}

// Entry is one enrolled, enabled device.
type Entry struct {
	ID             string `json:"id"`
	OrganizationID string `json:"organization_id"`
}

type index struct {
	version  string
	loadedAt time.Time
	orgs     map[string]string // device id -> organization id
}

// Directory answers "which organization owns this device?".
type Directory struct {
	cur      atomic.Pointer[index]
	maxStale time.Duration
	now      func() time.Time
}

// New creates an empty directory. maxStale <= 0 never expires a snapshot.
func New(maxStale time.Duration) *Directory {
	return &Directory{maxStale: maxStale, now: time.Now}
}

// SetClock replaces the time source (tests).
func (d *Directory) SetClock(now func() time.Time) { d.now = now }

// Load replaces the snapshot.
func (d *Directory) Load(s *Snapshot) {
	orgs := make(map[string]string, len(s.Devices))
	for _, e := range s.Devices {
		if e.ID != "" && e.OrganizationID != "" {
			orgs[strings.ToLower(e.ID)] = strings.ToLower(e.OrganizationID)
		}
	}
	d.cur.Store(&index{version: s.Version, loadedAt: d.now(), orgs: orgs})
}

// Touch marks the current snapshot as confirmed current (HTTP 304).
func (d *Directory) Touch() {
	if cur := d.cur.Load(); cur != nil {
		next := *cur
		next.loadedAt = d.now()
		d.cur.Store(&next)
	}
}

// Version is the loaded snapshot version, or "".
func (d *Directory) Version() string {
	if cur := d.cur.Load(); cur != nil {
		return cur.version
	}
	return ""
}

// Len is the number of known devices.
func (d *Directory) Len() int {
	if cur := d.cur.Load(); cur != nil {
		return len(cur.orgs)
	}
	return 0
}

// Ready reports whether lookups can be answered.
func (d *Directory) Ready() error {
	cur := d.cur.Load()
	if cur == nil || (d.maxStale > 0 && d.now().Sub(cur.loadedAt) > d.maxStale) {
		return ErrStale
	}
	return nil
}

// Organization returns the owner of an enrolled, enabled device.
func (d *Directory) Organization(deviceID string) (string, bool, error) {
	if err := d.Ready(); err != nil {
		return "", false, err
	}
	org, ok := d.cur.Load().orgs[strings.ToLower(deviceID)]
	return org, ok, nil
}

// Source fetches a snapshot; (nil, nil) means the known version is still current.
type Source interface {
	Fetch(ctx context.Context, knownVersion string) (*Snapshot, error)
}

// HTTPSource reads the control-plane endpoint.
type HTTPSource struct {
	URL    string
	Token  string
	Client *http.Client
}

func (h *HTTPSource) Fetch(ctx context.Context, knownVersion string) (*Snapshot, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(h.URL, "/")+"/internal/data-plane/devices", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Data-Plane-Internal-Token", h.Token)
	if knownVersion != "" {
		req.Header.Set("If-None-Match", `"`+knownVersion+`"`)
	}
	client := h.Client
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotModified {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("device directory endpoint returned HTTP %d", resp.StatusCode)
	}
	var s Snapshot
	if err := json.NewDecoder(io.LimitReader(resp.Body, 512<<20)).Decode(&s); err != nil {
		return nil, fmt.Errorf("decode device directory: %w", err)
	}
	return &s, nil
}

// FileSource reads a snapshot from a JSON file (local development, tests).
type FileSource struct{ Path string }

func (f *FileSource) Fetch(_ context.Context, known string) (*Snapshot, error) {
	raw, err := os.ReadFile(f.Path)
	if err != nil {
		return nil, err
	}
	var s Snapshot
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, err
	}
	if s.Version == "" {
		s.Version = fmt.Sprintf("file-%d", len(raw))
	}
	if s.Version == known {
		return nil, nil
	}
	return &s, nil
}

// Syncer refreshes the directory on an interval.
type Syncer struct {
	Dir      *Directory
	Source   Source
	Interval time.Duration
	Log      *slog.Logger
	Syncs    *prometheus.CounterVec // result: updated|unchanged|error (optional)
	Devices  prometheus.Gauge       // optional
}

// Once performs one refresh.
func (s *Syncer) Once(ctx context.Context) error {
	snap, err := s.Source.Fetch(ctx, s.Dir.Version())
	switch {
	case err != nil:
		s.count("error")
		return err
	case snap == nil:
		s.Dir.Touch()
		s.count("unchanged")
	default:
		s.Dir.Load(snap)
		s.count("updated")
		s.Log.Info("device directory updated", "version", snap.Version, "devices", len(snap.Devices))
	}
	if s.Devices != nil {
		s.Devices.Set(float64(s.Dir.Len()))
	}
	return nil
}

func (s *Syncer) count(result string) {
	if s.Syncs != nil {
		s.Syncs.WithLabelValues(result).Inc()
	}
}

// Run refreshes until ctx ends. Failures are retried sooner than the interval.
func (s *Syncer) Run(ctx context.Context) {
	for {
		wait := s.Interval
		cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		if err := s.Once(cctx); err != nil {
			s.Log.Warn("device directory refresh failed; serving the last snapshot", "error", err.Error())
			if wait > 5*time.Second {
				wait = 5 * time.Second
			}
		}
		cancel()
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
}
