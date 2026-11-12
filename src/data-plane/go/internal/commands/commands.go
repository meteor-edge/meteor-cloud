// Package commands delivers commands to applications and tracks them through an
// explicit state machine:
//
//	PENDING -> DISPATCHED -> RECEIVED -> EXECUTING -> SUCCEEDED | FAILED
//	any non-terminal state -> TIMEOUT
//
// The control plane (operators) and applications with a send_commands grant create
// commands; this package stores them, hands them to the transport, redelivers them
// until the target acknowledges receipt, and times them out. Delivery is at least
// once, so targets must treat the command id as an idempotency key.
package commands

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

var ErrForbidden = errors.New("not allowed for this command")

// InvalidError is a request the caller can fix.
type InvalidError struct{ Msg string }

func (e *InvalidError) Error() string { return e.Msg }

// Ack statuses a target reports.
const (
	AckReceived  = "received"
	AckExecuting = "executing"
	AckSucceeded = "succeeded"
	AckFailed    = "failed"
)

// Config tunes delivery.
type Config struct {
	AckTimeout      time.Duration // first wait for a "received" acknowledgement
	MaxBackoff      time.Duration // cap for redelivery backoff
	SweepInterval   time.Duration
	Lease           time.Duration // how long one replica owns a due command
	MaxPayloadBytes int
}

func (c *Config) defaults() {
	if c.AckTimeout <= 0 {
		c.AckTimeout = 5 * time.Second
	}
	if c.MaxBackoff <= 0 {
		c.MaxBackoff = time.Minute
	}
	if c.SweepInterval <= 0 {
		c.SweepInterval = time.Second
	}
	if c.Lease <= 0 {
		c.Lease = 30 * time.Second
	}
	if c.MaxPayloadBytes <= 0 {
		c.MaxPayloadBytes = 64 << 10
	}
}

// CreateRequest is a command to send. Organization and target come from the caller's
// verified context (control-plane internal API, or an application identity).
type CreateRequest struct {
	OrganizationID   string
	ApplicationID    string // target; or Application (slug)
	Application      string
	DeviceID         string
	Name             string
	Payload          json.RawMessage
	TimeoutSeconds   int
	ExpiresInSeconds int
	MaxAttempts      int // 0: redeliver until the command expires
	IdempotencyKey   string
	IssuedBy         string
}

// Service implements the state machine.
type Service struct {
	cfg     Config
	store   storage.Store
	bus     bus.Bus
	policy  *identity.Store
	metrics *observability.Metrics
	log     *slog.Logger
	now     func() time.Time
}

func NewService(cfg Config, store storage.Store, b bus.Bus, policy *identity.Store, metrics *observability.Metrics, log *slog.Logger) *Service {
	cfg.defaults()
	return &Service{cfg: cfg, store: store, bus: b, policy: policy, metrics: metrics, log: log, now: time.Now}
}

// SetClock replaces the time source (tests).
func (s *Service) SetClock(now func() time.Time) { s.now = now }

func (s *Service) backoff(attempts int) time.Duration {
	d := s.cfg.AckTimeout
	for i := 1; i < attempts && d < s.cfg.MaxBackoff; i++ {
		d *= 2
	}
	if d > s.cfg.MaxBackoff {
		d = s.cfg.MaxBackoff
	}
	return d
}

// Create validates, stores and dispatches a command.
func (s *Service) Create(ctx context.Context, req CreateRequest) (*storage.Command, error) {
	var app identity.SnapshotApplication
	var ok bool
	if req.ApplicationID != "" {
		app, ok = s.policy.Application(req.ApplicationID)
	} else {
		app, ok = s.policy.ApplicationBySlug(req.OrganizationID, req.Application)
	}
	if !ok || app.OrganizationID != req.OrganizationID {
		return nil, &InvalidError{"target application was not found in this organization"}
	}
	if !app.Permissions.ReceiveCommands {
		return nil, &InvalidError{"target application does not have the receive_commands permission"}
	}
	if req.Name == "" || len(req.Name) > 128 {
		return nil, &InvalidError{"name must be 1-128 characters"}
	}
	payload := req.Payload
	if len(payload) == 0 {
		payload = json.RawMessage(`{}`)
	}
	if len(payload) > s.cfg.MaxPayloadBytes {
		return nil, &InvalidError{fmt.Sprintf("payload is larger than %d bytes", s.cfg.MaxPayloadBytes)}
	}
	if payload[0] != '{' || !json.Valid(payload) {
		return nil, &InvalidError{"payload must be a JSON object"}
	}
	if req.TimeoutSeconds <= 0 {
		req.TimeoutSeconds = 60
	}
	if req.ExpiresInSeconds <= 0 {
		req.ExpiresInSeconds = 3600
	}
	if req.TimeoutSeconds > 86400 || req.ExpiresInSeconds > 7*86400 || req.MaxAttempts < 0 || req.MaxAttempts > 1000 {
		return nil, &InvalidError{"timeout_seconds, expires_in_seconds or max_attempts is out of range"}
	}
	if len(req.IdempotencyKey) > 128 {
		return nil, &InvalidError{"idempotency_key is longer than 128 characters"}
	}
	now := s.now().UTC()
	cmd := &storage.Command{
		ID: uuid.NewString(), OrganizationID: req.OrganizationID, ApplicationID: app.ID, Application: app.Slug,
		DeviceID: req.DeviceID, Name: req.Name, Payload: payload, State: storage.StatePending,
		IssuedBy: req.IssuedBy, IdempotencyKey: req.IdempotencyKey, MaxAttempts: req.MaxAttempts,
		TimeoutSeconds: req.TimeoutSeconds, CreatedAt: now, UpdatedAt: now,
		ExpiresAt: now.Add(time.Duration(req.ExpiresInSeconds) * time.Second), NextCheckAt: now,
	}
	stored, created, err := s.store.CreateCommand(ctx, cmd)
	if err != nil {
		return nil, err
	}
	if !created {
		return stored, nil // same idempotency key: the original command, not a second one
	}
	s.metrics.CommandsTotal.WithLabelValues(storage.StatePending).Inc()
	return s.dispatch(ctx, stored), nil
}

// dispatch hands the command to the transport and records the attempt. On transport
// failure the command stays PENDING and the sweeper retries.
func (s *Service) dispatch(ctx context.Context, cmd *storage.Command) *storage.Command {
	now := s.now().UTC()
	if err := s.bus.PublishCommand(ctx, cmd); err != nil {
		s.metrics.FailedDeliveries.WithLabelValues("command").Inc()
		s.log.Warn("command dispatch failed; will retry", "command_id", cmd.ID, "error", err.Error())
		next := now.Add(s.backoff(cmd.Attempts + 1))
		if updated, _, uerr := s.store.UpdateCommand(ctx, cmd.OrganizationID, cmd.ID, storage.CommandUpdate{
			From: []string{storage.StatePending, storage.StateDispatched}, IncAttempts: true, NextCheckAt: &next}); uerr == nil {
			return updated
		}
		return cmd
	}
	next := now.Add(s.backoff(cmd.Attempts + 1))
	updated, changed, err := s.store.UpdateCommand(ctx, cmd.OrganizationID, cmd.ID, storage.CommandUpdate{
		From: []string{storage.StatePending, storage.StateDispatched}, State: storage.StateDispatched,
		IncAttempts: true, DispatchedAt: &now, NextCheckAt: &next})
	if err != nil {
		s.metrics.StorageErrors.WithLabelValues("update_command").Inc()
		return cmd
	}
	if changed && cmd.State == storage.StatePending {
		s.metrics.CommandsTotal.WithLabelValues(storage.StateDispatched).Inc()
	}
	return updated
}

// Get returns a command of the organization.
func (s *Service) Get(ctx context.Context, organizationID, id string) (*storage.Command, error) {
	return s.store.GetCommand(ctx, organizationID, id)
}

// CanSee reports whether an application may read a command: its target or its issuer.
func CanSee(id *identity.Identity, cmd *storage.Command) bool {
	if cmd.OrganizationID != id.OrganizationID {
		return false
	}
	return isTarget(id, cmd) || cmd.IssuedBy == "app:"+id.ApplicationID
}

func isTarget(id *identity.Identity, cmd *storage.Command) bool {
	if cmd.OrganizationID != id.OrganizationID || cmd.ApplicationID != id.ApplicationID {
		return false
	}
	return cmd.DeviceID == "" || cmd.DeviceID == id.DeviceID
}

// Ack records a target's acknowledgement. Acknowledgements are idempotent and may
// arrive out of order: a late or repeated one never moves a command backwards.
func (s *Service) Ack(ctx context.Context, id *identity.Identity, commandID, status string, result json.RawMessage, errMsg string) (*storage.Command, error) {
	cmd, err := s.store.GetCommand(ctx, id.OrganizationID, commandID)
	if err != nil {
		return nil, err
	}
	if !id.Permissions.ReceiveCommands || !isTarget(id, cmd) {
		return nil, ErrForbidden
	}
	if len(result) > 0 && (len(result) > s.cfg.MaxPayloadBytes || !json.Valid(result)) {
		return nil, &InvalidError{"result must be valid JSON within the payload size limit"}
	}
	if len(errMsg) > 2000 {
		errMsg = errMsg[:2000]
	}
	now := s.now().UTC()
	var u storage.CommandUpdate
	switch status {
	case AckReceived:
		deadline := now.Add(time.Duration(cmd.TimeoutSeconds) * time.Second)
		u = storage.CommandUpdate{From: []string{storage.StatePending, storage.StateDispatched},
			State: storage.StateReceived, ReceivedAt: &now, NextCheckAt: &deadline}
	case AckExecuting:
		deadline := now.Add(time.Duration(cmd.TimeoutSeconds) * time.Second)
		u = storage.CommandUpdate{From: []string{storage.StatePending, storage.StateDispatched, storage.StateReceived},
			State: storage.StateExecuting, ReceivedAt: &now, NextCheckAt: &deadline}
	case AckSucceeded, AckFailed:
		state := storage.StateSucceeded
		if status == AckFailed {
			state = storage.StateFailed
		}
		u = storage.CommandUpdate{From: []string{storage.StatePending, storage.StateDispatched, storage.StateReceived, storage.StateExecuting},
			State: state, ReceivedAt: &now, CompletedAt: &now, Result: result}
		if errMsg != "" {
			u.Error = &errMsg
		}
	default:
		return nil, &InvalidError{"status must be received, executing, succeeded or failed"}
	}
	updated, changed, err := s.store.UpdateCommand(ctx, id.OrganizationID, commandID, u)
	if err != nil {
		return nil, err
	}
	if changed {
		s.metrics.CommandsTotal.WithLabelValues(updated.State).Inc()
	}
	return updated, nil
}

// Undelivered returns commands waiting for a target that just connected. They are
// handed to that connection directly, without waiting for the next redelivery.
func (s *Service) Undelivered(ctx context.Context, id *identity.Identity) ([]*storage.Command, error) {
	if !id.Permissions.ReceiveCommands {
		return nil, nil
	}
	return s.store.UndeliveredCommands(ctx, id.OrganizationID, id.ApplicationID, id.DeviceID, 200)
}

// Sweep processes due commands once: redelivery and timeouts.
func (s *Service) Sweep(ctx context.Context) error {
	now := s.now().UTC()
	due, err := s.store.ClaimDueCommands(ctx, now, s.cfg.Lease, 200)
	if err != nil {
		s.metrics.StorageErrors.WithLabelValues("claim_commands").Inc()
		return err
	}
	lag := 0.0
	for _, cmd := range due {
		if age := now.Sub(cmd.UpdatedAt).Seconds(); age > lag {
			lag = age
		}
		switch cmd.State {
		case storage.StatePending, storage.StateDispatched:
			exhausted := cmd.MaxAttempts > 0 && cmd.Attempts >= cmd.MaxAttempts
			if !now.Before(cmd.ExpiresAt) || exhausted {
				reason := "expired before the target acknowledged it"
				if exhausted {
					reason = fmt.Sprintf("not acknowledged after %d delivery attempts", cmd.Attempts)
				}
				s.timeout(ctx, cmd, []string{storage.StatePending, storage.StateDispatched}, reason)
				continue
			}
			if cmd.Attempts > 0 {
				s.metrics.CommandRetries.Inc()
			}
			s.dispatch(ctx, cmd)
		case storage.StateReceived, storage.StateExecuting:
			// Claimed because its execution deadline passed.
			s.timeout(ctx, cmd, []string{storage.StateReceived, storage.StateExecuting},
				fmt.Sprintf("no result within %d seconds of being received", cmd.TimeoutSeconds))
		}
	}
	s.metrics.DispatcherLag.Set(lag)
	return nil
}

func (s *Service) timeout(ctx context.Context, cmd *storage.Command, from []string, reason string) {
	now := s.now().UTC()
	_, changed, err := s.store.UpdateCommand(ctx, cmd.OrganizationID, cmd.ID, storage.CommandUpdate{
		From: from, State: storage.StateTimeout, CompletedAt: &now, Error: &reason})
	if err != nil {
		s.metrics.StorageErrors.WithLabelValues("update_command").Inc()
		return
	}
	if changed {
		s.metrics.CommandsTotal.WithLabelValues(storage.StateTimeout).Inc()
	}
}

// Run sweeps until ctx is cancelled.
func (s *Service) Run(ctx context.Context) {
	ticker := time.NewTicker(s.cfg.SweepInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := s.Sweep(ctx); err != nil && !errors.Is(err, context.Canceled) {
				s.log.Warn("command sweep failed", "error", err.Error())
			}
		}
	}
}
