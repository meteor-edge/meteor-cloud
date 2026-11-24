package identity

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"meteorcloud/meteorcli/internal/api"
)

// Poll results of pollOnce (the server's states plus what happened locally).
const (
	PollPending  = api.PollPending
	PollApproved = api.PollApproved
	PollRejected = api.PollRejected
	PollExpired  = api.PollExpired
	// PollGone: the server does not know the request (401/403/404); it was dropped.
	PollGone = "unknown"
	// PollFailed: the poll did not get an answer; it is tried again later.
	PollFailed = "failed"
)

// Error codes the socket passes on to the CLI.
const (
	CodeNotConfigured   = "not_configured"
	CodeNoAPIKey        = "no_api_key"
	CodeAlreadyEnrolled = "already_enrolled"
	CodeAlreadyPending  = "already_pending"
	CodeNoPending       = "no_pending"
	CodeKeyRejected     = "key_rejected" // the API key is wrong, revoked or expired
	CodeRetryLater      = "retry_later"  // network down, server busy or rate limited
	CodeServerError     = "server_error" // any other refusal; Message is the server's
	CodeRequestEnded    = "request_ended"
)

// Error is a failed enrollment step. RetryAfter is the server's Retry-After;
// Status the HTTP status when the server answered (0 otherwise).
type Error struct {
	Code       string
	Message    string
	RetryAfter time.Duration
	Status     int
}

func (e *Error) Error() string { return e.Message }

func errorf(code, format string, args ...any) *Error {
	return &Error{Code: code, Message: fmt.Sprintf(format, args...)}
}

// fromAPI turns a failed API call into an Error the CLI can map to an exit code.
func fromAPI(what string, err error) *Error {
	var e *api.Error
	if !errors.As(err, &e) {
		return errorf(CodeServerError, "%s: %v", what, err)
	}
	out := &Error{Code: CodeServerError, Message: fmt.Sprintf("%s: %s", what, e.Message), RetryAfter: e.RetryAfter, Status: e.Status}
	switch {
	case e.Status == http.StatusUnauthorized || e.Status == http.StatusForbidden:
		out.Code = CodeKeyRejected
	case api.Retryable(err):
		out.Code = CodeRetryLater
	}
	return out
}

// Enrollment is the non-secret result of a successful enrollment.
type Enrollment struct {
	DeviceID         string `json:"device_id"`
	OrganizationID   string `json:"organization_id"`
	Name             string `json:"name"`
	HeartbeatSeconds int    `json:"heartbeat_seconds"`
}

// Register enrolls with a registration token. When the device already has a
// token it is sent too: it proves this is the same hardware. Enrollment files
// belong to one server (`meteorcli config` drops them when the server
// changes), so the stored token always goes to the server that issued it.
func (s *Service) Register(ctx context.Context, token, name string) (Enrollment, error) {
	s.defaults()
	s.mu.Lock()
	defer s.mu.Unlock()
	sn, err := s.target()
	if err != nil {
		return Enrollment{}, err
	}
	e, err := s.API.Register(ctx, token, s.Inventory(), name, sn.token)
	if err != nil {
		return Enrollment{}, fromAPI("registration failed", err)
	}
	return s.enrolled(*e, "registered", "")
}

// enrolled stores a new identity from the server the client talks to, tells
// the agent, and returns its summary. claimID is the request it answers, if
// any. The caller holds s.mu.
func (s *Service) enrolled(e api.Enrollment, how, claimID string) (Enrollment, error) {
	if e.DeviceID == "" || e.DeviceToken == "" {
		return Enrollment{}, errorf(CodeServerError, "the server answered without a device id or token")
	}
	if err := s.Source.commit(e, s.applied, claimID); err != nil {
		if errors.Is(err, errStale) {
			s.reloadDue = true
			return Enrollment{}, errorf(CodeServerError, "%v", err)
		}
		return Enrollment{}, err
	}
	s.autoAsk = false
	s.nextMQTT = time.Now()
	s.Log.Info("device "+how, "device_id", e.DeviceID, "organization_id", e.OrganizationID, "name", e.Name)
	if s.Events.Enrolled != nil {
		s.Events.Enrolled()
	}
	s.poke()
	return Enrollment{
		DeviceID: e.DeviceID, OrganizationID: e.OrganizationID, Name: e.Name,
		HeartbeatSeconds: int(s.Source.Heartbeat().Seconds()),
	}, nil
}

// RequestOptions are the inputs of request-token.
type RequestOptions struct {
	// APIKey, when set, is stored before it is used. Otherwise the saved key is used.
	APIKey string
	// Name is the device name to ask for.
	Name string
	// New submits a new request even when one is pending or the device is enrolled.
	New bool
}

// RequestResult says what request-token did.
type RequestResult struct {
	// Status is "submitted" or "approved" (a pending request was approved meanwhile).
	Status string `json:"status"`
	// Previous is how an older request ended, when a new one replaced it.
	Previous            string      `json:"previous,omitempty"`
	RequestID           string      `json:"request_id,omitempty"`
	PollIntervalSeconds int         `json:"poll_interval_seconds,omitempty"`
	Enrollment          *Enrollment `json:"enrollment,omitempty"`
}

// RequestToken asks an operator to approve this device. The agent then polls
// the request in the background until it is approved, also across reboots.
func (s *Service) RequestToken(ctx context.Context, o RequestOptions) (RequestResult, error) {
	s.defaults()
	s.mu.Lock()
	defer s.mu.Unlock()
	sn, err := s.target()
	if err != nil {
		return RequestResult{}, err
	}
	// Refuse before storing anything: a refused command must not replace the saved key.
	if sn.enrolled && !o.New {
		st := s.Source.Status()
		return RequestResult{}, errorf(CodeAlreadyEnrolled,
			"this device is already enrolled as %s (%s); use --new to enroll it again", st.Name, st.DeviceID)
	}
	if o.APIKey != "" {
		if err := s.Source.saveAPIKey(o.APIKey); err != nil {
			return RequestResult{}, err
		}
		sn.apiKey = o.APIKey
	}
	if sn.apiKey == "" {
		return RequestResult{}, errorf(CodeNoAPIKey, "an API key is required (use --api-key, $METEORCLI_API_KEY, or 'meteorcli config --api-key')")
	}
	var previous string
	if sn.pending != nil && !o.New {
		// Asking again must not poll faster than the schedule or the server allows.
		if s.approved != nil || time.Now().Before(s.nextPoll) {
			return RequestResult{}, errorf(CodeAlreadyPending,
				"enrollment request %s is already waiting for approval; the agent connects as soon as it is approved. "+
					"Use --new to submit a different request", sn.pending.RequestID)
		}
		switch got := s.pollOnce(ctx); got {
		case PollApproved:
			st := s.Source.Status()
			return RequestResult{Status: PollApproved, Enrollment: &Enrollment{
				DeviceID: st.DeviceID, OrganizationID: st.OrganizationID, Name: st.Name, HeartbeatSeconds: st.HeartbeatSeconds,
			}}, nil
		case PollPending, PollFailed:
			return RequestResult{}, errorf(CodeAlreadyPending,
				"enrollment request %s is already waiting for approval; the agent connects as soon as it is approved. "+
					"Use --new to submit a different request", sn.pending.RequestID)
		default:
			previous = got
		}
	}
	name := o.Name
	if name == "" && sn.pending != nil {
		name = sn.pending.Name
	}
	res, err := s.submit(ctx, sn.apiKey, name)
	if err != nil {
		return RequestResult{}, err
	}
	s.autoAsk = false
	res.Previous = previous
	return res, nil
}

// submit sends enroll/request and stores the claim. The caller holds s.mu.
func (s *Service) submit(ctx context.Context, apiKey, name string) (RequestResult, error) {
	r, err := s.API.EnrollRequest(ctx, apiKey, s.Inventory(), name)
	if err != nil {
		return RequestResult{}, fromAPI("enrollment request failed", err)
	}
	if r.RequestID == "" || r.ClaimSecret == "" {
		return RequestResult{}, errorf(CodeServerError, "the server answered without a request id or claim secret")
	}
	if err := s.Source.savePending(&PendingClaim{RequestID: r.RequestID, ClaimSecret: r.ClaimSecret, Name: name}); err != nil {
		return RequestResult{}, err
	}
	interval := s.pollInterval(r.PollIntervalSeconds)
	s.nextPoll = time.Now().Add(interval)
	s.Log.Info("enrollment request submitted; waiting for approval in MeteorCloud",
		"request_id", r.RequestID, "poll_every", interval)
	s.poke()
	return RequestResult{Status: "submitted", RequestID: r.RequestID, PollIntervalSeconds: int(interval.Seconds())}, nil
}

// autoSubmit submits the request the agent makes by itself. A refused key
// stops it; other failures are retried every minute. The caller holds s.mu.
func (s *Service) autoSubmit(ctx context.Context, sn snapshot) {
	_, err := s.submit(ctx, sn.apiKey, sn.enrollName)
	if err == nil {
		s.autoAsk = false
		return
	}
	var e *Error
	if errors.As(err, &e) && e.Code == CodeKeyRejected {
		s.autoAsk = false
		if err := s.Source.markAutoRefused(sn.apiKey); err != nil {
			s.Log.Error("remember the refused API key", "err", err)
		}
		s.Log.Error("the saved API key was refused; run 'meteorcli config --api-key key_…' and 'meteorcli request-token'", "err", err)
		return
	}
	wait := time.Minute
	if errors.As(err, &e) && e.RetryAfter > wait {
		wait = e.RetryAfter
	}
	s.nextAsk = time.Now().Add(wait)
	s.Log.Warn("enrollment request failed; trying again", "err", err, "retry_in", wait)
}

// pollInterval is the server's suggestion, never faster than MinPoll.
func (s *Service) pollInterval(suggested int) time.Duration {
	return max(s.MinPoll, time.Duration(suggested)*time.Second)
}

// pollOnce asks whether the pending request was approved and acts on the
// answer. It returns one of the Poll* results. The caller holds s.mu.
func (s *Service) pollOnce(ctx context.Context) string {
	claim := s.Source.snapshot().pending
	if claim == nil {
		return PollGone
	}
	now := time.Now()
	s.lastPoll = now
	r, err := s.API.EnrollPoll(ctx, claim.RequestID, claim.ClaimSecret)
	// Claim waits for this; it runs after the answer was acted on (an approval
	// stored, a request dropped), so Claim sees the result, not a stale state.
	defer s.Source.markChecked(claim.RequestID, now)
	if err != nil {
		switch status := api.StatusOf(err); {
		case status == 401 || status == 403 || status == 404:
			s.Log.Error("the server does not know this enrollment request; submit a new one with 'meteorcli request-token'",
				"request_id", claim.RequestID, "err", err)
			s.drop(claim, PollGone, err.Error())
			return PollGone
		case status == 429:
			var e *api.Error
			wait := s.RetryWait
			if errors.As(err, &e) && e.RetryAfter > 0 {
				wait = max(s.MinPoll, e.RetryAfter)
			}
			s.nextPoll = now.Add(wait)
			s.backoffUntil = s.nextPoll
			s.Log.Info("the server asked to poll more slowly", "retry_in", wait)
		default:
			s.nextPoll = now.Add(s.RetryWait)
			s.Log.Warn("could not check enrollment", "err", err, "retry_in", s.RetryWait)
		}
		return PollFailed
	}
	s.backoffUntil = time.Time{}
	switch r.Status {
	case PollPending:
		s.nextPoll = now.Add(s.pollInterval(r.PollIntervalSeconds))
		return PollPending
	case PollApproved:
		if r.DeviceToken != "" {
			s.approved = &approval{enrollment: r.Enrollment, claimID: claim.RequestID}
			if s.retryApproved() {
				return PollApproved
			}
			return PollFailed
		}
		// The token is handed out once and someone else collected it. An identity
		// already installed (request-token --new) is not this approval: keep it.
		reason := "approved, but the device credential was already collected; submit a new request"
		if s.Source.Enrolled() {
			reason = "approved, but the new device credential was already collected; the current identity is kept"
		}
		s.Log.Error(reason, "request_id", claim.RequestID)
		s.drop(claim, PollRejected, reason)
		return PollRejected
	case PollRejected:
		reason := "no reason given"
		if r.RejectionReason != nil && *r.RejectionReason != "" {
			reason = *r.RejectionReason
		}
		s.Log.Warn("enrollment request rejected", "request_id", claim.RequestID, "reason", reason)
		s.drop(claim, PollRejected, reason)
		return PollRejected
	case PollExpired:
		return PollExpired
	default:
		s.nextPoll = now.Add(s.RetryWait)
		s.Log.Warn("unknown enrollment status; checking again later", "status", r.Status)
		return PollFailed
	}
}

// resubmit replaces an expired request using the saved API key, as the Python
// service did. Without a key, or with a refused one, the request ends.
func (s *Service) resubmit(ctx context.Context) {
	sn := s.Source.snapshot()
	old := sn.pending
	if old == nil {
		return
	}
	if sn.apiKey == "" {
		s.Log.Error("enrollment request expired and no API key is saved to submit a new one; "+
			"run 'meteorcli config --api-key key_…' and 'meteorcli request-token'", "request_id", old.RequestID)
		s.drop(old, PollExpired, "expired")
		return
	}
	_, err := s.submit(ctx, sn.apiKey, old.Name)
	var e *Error
	switch {
	case err == nil:
		s.Log.Info("enrollment request expired; submitted a new one", "old_request_id", old.RequestID)
	case errors.As(err, &e) && (e.Code == CodeKeyRejected || e.Status == http.StatusNotFound):
		s.Log.Error("enrollment request expired and the API key was refused", "err", err)
		s.drop(old, PollExpired, err.Error())
	default:
		wait := s.RetryWait
		if errors.As(err, &e) {
			wait = max(wait, e.RetryAfter)
		}
		s.nextPoll = time.Now().Add(wait)
		s.Log.Warn("enrollment request expired; submitting a new one failed, will retry", "err", err, "retry_in", wait)
	}
}

// retryApproved stores the identity of a collected approval. When storing
// fails it is kept and tried again after RetryWait (the server will not hand
// the token out again); an approval for a server or request that was
// replaced is discarded. It reports success. The caller holds s.mu.
func (s *Service) retryApproved() bool {
	a := s.approved
	_, err := s.enrolled(a.enrollment, "approved", a.claimID)
	var e *Error
	switch {
	case err == nil:
		s.approved = nil
		return true
	case errors.As(err, &e) && e.Code == CodeServerError:
		s.approved = nil
		s.Log.Warn("approval discarded", "err", err)
	default:
		s.nextPoll = time.Now().Add(s.RetryWait)
		s.Log.Error("store the approved identity; trying again", "err", err, "retry_in", s.RetryWait)
	}
	return false
}

// drop ends the pending request. If its file cannot be removed the request is
// left for later instead of being polled again at every wake.
func (s *Service) drop(claim *PendingClaim, status, reason string) {
	if err := s.Source.dropPending(&Outcome{RequestID: claim.RequestID, Status: status, Reason: reason}); err != nil {
		s.nextPoll = time.Now().Add(s.RetryWait)
		s.Log.Error("remove pending request", "err", err, "retry_in", s.RetryWait)
	}
}
