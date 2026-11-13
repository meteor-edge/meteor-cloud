package rules

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
)

// SnapshotSource fetches the control plane's rule snapshot; (nil, nil) means the
// known version is still current.
type SnapshotSource interface {
	Fetch(ctx context.Context, knownVersion string) (*engine.Snapshot, error)
}

// HTTPSnapshotSource reads GET /internal/rules/snapshot.
type HTTPSnapshotSource struct {
	URL    string
	Token  string
	Client *http.Client
}

func (h *HTTPSnapshotSource) Fetch(ctx context.Context, known string) (*engine.Snapshot, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(h.URL, "/")+"/internal/rules/snapshot", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Data-Plane-Internal-Token", h.Token)
	if known != "" {
		req.Header.Set("If-None-Match", `"`+known+`"`)
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
		return nil, fmt.Errorf("rule snapshot endpoint answered HTTP %d", resp.StatusCode)
	}
	var s engine.Snapshot
	if err := json.NewDecoder(io.LimitReader(resp.Body, 512<<20)).Decode(&s); err != nil {
		return nil, fmt.Errorf("decode rule snapshot: %w", err)
	}
	return &s, nil
}

// Syncer keeps the engine on the control plane's current rules. After each change it
// reconciles: state of removed or changed rules is retired (their open alerts
// resolve) and absence timers of new rules are seeded. A failed reconcile is retried
// on the next pass, so no retirement is lost.
type Syncer struct {
	Engine   *engine.Engine
	Source   SnapshotSource
	Interval time.Duration
	Limits   dsl.Limits
	Log      *slog.Logger

	unreconciled *engine.RuleSet // previous rule set while a reconcile is owed
	owed         bool
}

// Once performs one refresh.
func (s *Syncer) Once(ctx context.Context) error {
	known := ""
	if cur := s.Engine.Rules(); cur != nil {
		known = cur.Version
	}
	snap, err := s.Source.Fetch(ctx, known)
	if err != nil {
		return err
	}
	if snap != nil {
		rs := engine.CompileSnapshot(snap, s.Limits)
		old := s.Engine.Apply(rs)
		if !s.owed {
			s.unreconciled, s.owed = old, true
		}
		s.Log.Info("rule snapshot loaded", "version", rs.Version, "rules", rs.Len(), "refused", len(rs.Errors))
	}
	if s.owed {
		if err := s.Engine.Reconcile(ctx, s.unreconciled, s.Engine.Rules()); err != nil {
			return fmt.Errorf("reconcile rules: %w", err)
		}
		s.unreconciled, s.owed = nil, false
	}
	return nil
}

// Run refreshes until ctx ends; failures are retried within 5 seconds.
func (s *Syncer) Run(ctx context.Context) {
	for {
		wait := s.Interval
		cctx, cancel := context.WithTimeout(ctx, time.Minute)
		if err := s.Once(cctx); err != nil && ctx.Err() == nil {
			s.Log.Warn("rule snapshot refresh failed", "error", err.Error())
			wait = min(wait, 5*time.Second)
		}
		cancel()
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
}
