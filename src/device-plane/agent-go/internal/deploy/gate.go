package deploy

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Health gate defaults (docs/fleet/rollback.md), as in the Python agent.
const (
	defaultGateTimeout = 60 * time.Second
	defaultGateStable  = 10 * time.Second
	gatePoll           = 2 * time.Second
	probeTimeout       = 5 * time.Second
)

// gate is a release's health_check: after an update the workload must become
// healthy (runtime, plus http_url answering 2xx/3xx) within Timeout and stay
// so for Stable; otherwise the update failed and, with Rollback, the previous
// release is applied again.
type gate struct {
	Timeout  time.Duration
	Stable   time.Duration
	URL      string
	Rollback bool
}

// gateOf returns the spec's health_check with defaults, nil when it has none.
func gateOf(spec runtime.Spec) *gate {
	raw, ok := spec["health_check"].(map[string]any)
	if !ok {
		return nil
	}
	hc := runtime.Spec(raw)
	g := &gate{Timeout: defaultGateTimeout, Stable: defaultGateStable, URL: hc.Text("http_url", ""), Rollback: true}
	if n := toInt(raw["timeout_seconds"]); n != 0 {
		g.Timeout = time.Duration(n) * time.Second
	}
	if v, ok := raw["stable_seconds"]; ok && v != nil {
		g.Stable = time.Duration(toInt(v)) * time.Second
	}
	if v, ok := raw["rollback"].(bool); ok && !v {
		g.Rollback = false
	}
	return g
}

// clock is time for the gate; tests replace it.
type clock interface {
	Now() time.Time
	// Sleep waits d and returns false when ctx ended first.
	Sleep(ctx context.Context, d time.Duration) bool
}

type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }

func (realClock) Sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// wait returns "" once the workload stayed healthy for g.Stable, otherwise the
// last problem. A workload that turns unhealthy again starts the stable
// window over; one still starting (Unknown) is neither.
func (g gate) wait(ctx context.Context, check func(context.Context) runtime.Health, probe func(context.Context, string) string, clk clock) string {
	started := clk.Now()
	deadline := started.Add(g.Timeout + g.Stable)
	var healthySince time.Time
	problem := ""
	notHealthy := func() string {
		if problem != "" {
			return problem
		}
		return fmt.Sprintf("not healthy within %ds", int(g.Timeout/time.Second))
	}
	for {
		h := check(ctx)
		healthy, why := h.Status == runtime.Healthy, h.Problem
		if healthy && g.URL != "" {
			why = probe(ctx, g.URL)
			healthy = why == ""
		}
		// The observation is from when the check finished: a slow check must
		// not count its own duration as time spent healthy.
		now := clk.Now()
		if healthy {
			if healthySince.IsZero() {
				healthySince = now
			}
			if now.Sub(healthySince) >= g.Stable {
				return ""
			}
		} else {
			healthySince = time.Time{}
			if why != "" {
				problem = why
			}
			if now.Sub(started) >= g.Timeout {
				return notHealthy()
			}
		}
		if !now.Before(deadline) {
			return notHealthy()
		}
		if !clk.Sleep(ctx, gatePoll) {
			return "the agent is stopping"
		}
	}
}

// httpProbe returns "" when url answers 2xx or 3xx (redirects not followed).
func httpProbe(ctx context.Context, url string) string {
	ctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Sprintf("%s did not answer (%v)", url, err)
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Sprintf("%s did not answer (%v)", url, err)
	}
	resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 400 {
		return ""
	}
	return fmt.Sprintf("%s answered HTTP %d", url, resp.StatusCode)
}
