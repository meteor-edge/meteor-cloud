package identity

// Claim and Test: what `meteorcli claim` and `meteorcli test` ask the agent.

import (
	"context"
	"net/http"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
)

// ClaimResult says where a pending request stands after Claim.
type ClaimResult struct {
	// Status is "approved" (enrolled now) or "pending" (still waiting).
	Status     string      `json:"status"`
	RequestID  string      `json:"request_id,omitempty"`
	Enrollment *Enrollment `json:"enrollment,omitempty"`
}

// Claim waits up to wait for the pending request to be approved. The
// background poller does the polling (never faster than MinPoll); Claim only
// asks it to poll as soon as allowed and waits for at least one answer.
func (s *Service) Claim(ctx context.Context, wait time.Duration) (ClaimResult, error) {
	s.defaults()
	start := time.Now()
	deadline := start.Add(wait)
	s.mu.Lock()
	if s.Source.snapshot().pending != nil {
		allowed := s.lastPoll.Add(s.MinPoll)
		if allowed.Before(start) {
			allowed = start
		}
		s.nextPoll = earliest(s.nextPoll, allowed)
	}
	s.mu.Unlock()
	s.poke()
	timer := time.NewTimer(max(wait, 0))
	defer timer.Stop()
	for {
		changed := s.Source.Changed()
		st := s.Source.Status()
		switch {
		case st.PendingRequestID == "" && st.Outcome != nil && st.Outcome.Status != PollApproved:
			return ClaimResult{}, outcomeError(st.Outcome)
		case st.PendingRequestID == "" && (st.State == StateEnrolled || st.State == StateRejected):
			return ClaimResult{Status: PollApproved, Enrollment: &Enrollment{
				DeviceID: st.DeviceID, OrganizationID: st.OrganizationID, Name: st.Name, HeartbeatSeconds: st.HeartbeatSeconds,
			}}, nil
		case st.PendingRequestID == "":
			return ClaimResult{}, errorf(CodeNoPending, "no pending enrollment request; run 'meteorcli request-token' first")
		case !st.PendingChecked.Before(start) && !time.Now().Before(deadline):
			return ClaimResult{Status: PollPending, RequestID: st.PendingRequestID}, nil
		}
		select {
		case <-ctx.Done():
			return ClaimResult{Status: PollPending, RequestID: st.PendingRequestID}, nil
		case <-changed:
		case <-timer.C: // the deadline passed; wait for the first answer if none came yet
		}
	}
}

func outcomeError(o *Outcome) error {
	switch o.Status {
	case PollRejected:
		return errorf(CodeRequestEnded, "enrollment rejected: %s", o.Reason)
	case PollExpired:
		return errorf(CodeRequestEnded, "enrollment request expired; submit a new one with 'meteorcli request-token'")
	default:
		return errorf(CodeRequestEnded, "the server does not know enrollment request %s; submit a new one with 'meteorcli request-token'", o.RequestID)
	}
}

func errNotConfigured() error {
	return errorf(CodeNotConfigured, "no server configured; run 'sudo meteorcli config --domain <host>' first")
}

// TestResult is what `meteorcli test` shows. It holds no secrets.
type TestResult struct {
	Server           string `json:"server"`
	APIKey           string `json:"api_key"` // "ok", "missing" or "rejected"
	APIKeyError      string `json:"api_key_error,omitempty"`
	OrganizationID   string `json:"organization_id,omitempty"`
	OrganizationName string `json:"organization_name,omitempty"`
	KeyName          string `json:"key_name,omitempty"`
	KeyPrefix        string `json:"key_prefix,omitempty"`
}

// Test checks that the server answers and that the saved API key is valid,
// without creating an enrollment request.
func (s *Service) Test(ctx context.Context) (TestResult, error) {
	sn := s.Source.snapshot()
	if sn.server == "" {
		return TestResult{}, errNotConfigured()
	}
	r := TestResult{Server: sn.server, APIKey: "missing"}
	if err := s.API.Health(ctx); err != nil {
		e := fromAPI("cannot reach "+sn.server, err)
		if api.StatusOf(err) == http.StatusNotFound && config.LANHostMissingPort(sn.server) {
			e.Message += " (hint: that URL is port 80; laptop `make dev` is :8000 — run 'meteorcli config --domain <ip>:8000')"
		}
		return r, e
	}
	if sn.apiKey == "" {
		return r, nil
	}
	c, err := s.API.EnrollCheck(ctx, sn.apiKey)
	if err != nil {
		r.APIKey, r.APIKeyError = "rejected", fromAPI("API key", err).Message
		return r, nil
	}
	r.APIKey = "ok"
	r.OrganizationID, r.OrganizationName, r.KeyName, r.KeyPrefix = c.OrganizationID, c.OrganizationName, c.KeyName, c.KeyPrefix
	return r, nil
}
