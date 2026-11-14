package identity

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
	"time"
)

// Source fetches the policy snapshot. It returns (nil, nil) when the snapshot the
// store already holds (by version) is still current.
type Source interface {
	Fetch(ctx context.Context, knownVersion string) (*Snapshot, error)
}

// HTTPSource reads GET {control plane}/internal/data-plane/policy.
type HTTPSource struct {
	URL    string
	Token  string
	Client *http.Client
}

// noRedirectClient never follows a redirect: the internal token must not travel to
// an address other than the configured control plane.
var noRedirectClient = &http.Client{
	Timeout:       30 * time.Second,
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
}

func (h *HTTPSource) Fetch(ctx context.Context, knownVersion string) (*Snapshot, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(h.URL, "/")+"/internal/data-plane/policy", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Data-Plane-Internal-Token", h.Token)
	if knownVersion != "" {
		req.Header.Set("If-None-Match", `"`+knownVersion+`"`)
	}
	client := h.Client
	if client == nil {
		client = noRedirectClient
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
		return nil, fmt.Errorf("policy endpoint returned HTTP %d", resp.StatusCode)
	}
	var snap Snapshot
	if err := json.NewDecoder(io.LimitReader(resp.Body, 256<<20)).Decode(&snap); err != nil {
		return nil, fmt.Errorf("decode policy: %w", err)
	}
	return &snap, nil
}

// FileSource reads a snapshot from a JSON file: local development and tests without
// a control plane. The file is re-read on every poll.
type FileSource struct{ Path string }

func (f *FileSource) Fetch(_ context.Context, knownVersion string) (*Snapshot, error) {
	raw, err := os.ReadFile(f.Path)
	if err != nil {
		return nil, err
	}
	var snap Snapshot
	if err := json.Unmarshal(raw, &snap); err != nil {
		return nil, fmt.Errorf("decode policy file: %w", err)
	}
	if snap.Version == "" {
		snap.Version = HashToken(string(raw))[:32]
	}
	if snap.Version == knownVersion {
		return nil, nil
	}
	return &snap, nil
}

// SyncObserver receives sync outcomes (metrics).
type SyncObserver interface {
	PolicySync(ok bool, credentials int)
}

// Syncer keeps a Store up to date.
type Syncer struct {
	Store    *Store
	Source   Source
	Interval time.Duration
	Log      *slog.Logger
	Observer SyncObserver
}

// SyncOnce fetches and applies the snapshot once.
func (s *Syncer) SyncOnce(ctx context.Context) error {
	version, _ := s.Store.Version()
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	snap, err := s.Source.Fetch(ctx, version)
	if err != nil {
		if s.Observer != nil {
			s.Observer.PolicySync(false, -1)
		}
		return err
	}
	if snap == nil {
		s.Store.Touch()
		if s.Observer != nil {
			s.Observer.PolicySync(true, -1)
		}
		return nil
	}
	s.Store.Apply(snap)
	if s.Observer != nil {
		s.Observer.PolicySync(true, len(snap.Credentials))
	}
	s.Log.Info("policy snapshot applied", "version", snap.Version,
		"applications", len(snap.Applications), "credentials", len(snap.Credentials))
	return nil
}

// Run polls until ctx is cancelled. A failed poll keeps the previous snapshot.
func (s *Syncer) Run(ctx context.Context) {
	failing := false
	for {
		if err := s.SyncOnce(ctx); err != nil && !errors.Is(err, context.Canceled) {
			if !failing {
				s.Log.Warn("policy sync failed; serving the last snapshot", "error", err.Error())
			}
			failing = true
		} else if failing {
			s.Log.Info("policy sync recovered")
			failing = false
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(s.Interval):
		}
	}
}
