// Package storage is the persistence boundary of the data plane. Applications never
// see it: they use the HTTP API. Events live in an EventStore (ClickHouse, PostgreSQL
// or memory); commands and dead letters need row updates and live in PostgreSQL.
package storage

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"meteorcloud/dataplane/internal/envelope"
)

var ErrNotFound = errors.New("not found")

// Cursor is a keyset position in (time, id) order.
type Cursor struct {
	Time time.Time
	ID   string
}

// Query selects stored events of one organization and stream.
type Query struct {
	OrganizationID string
	Stream         string
	Kind           string // optional
	DeviceID       string // optional
	Application    string // optional application slug
	Start, End     time.Time
	Limit          int
	Descending     bool
	After          *Cursor
	// Filter is a JSON object the event data must contain (top-level equality).
	Filter map[string]json.RawMessage
}

// AggregateQuery buckets a numeric field over time.
type AggregateQuery struct {
	Query
	Field  []string // path inside data, e.g. ["power"] or ["phases","l1"]
	Func   string   // avg, min, max, sum, count
	Bucket time.Duration
}

// Bucket is one aggregation result. Value is nil when the bucket has no numeric values.
type Bucket struct {
	Start time.Time `json:"start"`
	Value *float64  `json:"value"`
	Count int64     `json:"count"`
}

// DeadLetter is a message that could not be processed and cannot be reported to its
// sender (MQTT ingestion).
type DeadLetter struct {
	ReceivedAt     time.Time
	Source         string
	OrganizationID string
	Reason         string
	Detail         string
	Payload        []byte
}

// Command states (contracts/data-plane-v1.md).
const (
	StatePending    = "PENDING"
	StateDispatched = "DISPATCHED"
	StateReceived   = "RECEIVED"
	StateExecuting  = "EXECUTING"
	StateSucceeded  = "SUCCEEDED"
	StateFailed     = "FAILED"
	StateTimeout    = "TIMEOUT"
)

// Terminal reports whether a command state is final.
func Terminal(state string) bool {
	return state == StateSucceeded || state == StateFailed || state == StateTimeout
}

// Command is a unit of work addressed to an application.
type Command struct {
	ID             string          `json:"id"`
	OrganizationID string          `json:"organization_id"`
	ApplicationID  string          `json:"application_id"`
	Application    string          `json:"application"`
	DeviceID       string          `json:"device_id,omitempty"`
	Name           string          `json:"name"`
	Payload        json.RawMessage `json:"payload"`
	State          string          `json:"state"`
	IssuedBy       string          `json:"issued_by"`
	IdempotencyKey string          `json:"idempotency_key,omitempty"`
	Attempts       int             `json:"attempts"`
	MaxAttempts    int             `json:"max_attempts"`
	TimeoutSeconds int             `json:"timeout_seconds"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
	ExpiresAt      time.Time       `json:"expires_at"`
	DispatchedAt   *time.Time      `json:"dispatched_at,omitempty"`
	ReceivedAt     *time.Time      `json:"received_at,omitempty"`
	CompletedAt    *time.Time      `json:"completed_at,omitempty"`
	Result         json.RawMessage `json:"result,omitempty"`
	Error          string          `json:"error,omitempty"`
	// NextCheckAt is when the dispatcher looks at the command again (retry or timeout).
	NextCheckAt time.Time `json:"-"`
}

// CommandUpdate is applied atomically when the command is in one of the From states.
type CommandUpdate struct {
	From         []string
	State        string
	IncAttempts  bool
	DispatchedAt *time.Time
	ReceivedAt   *time.Time
	CompletedAt  *time.Time
	Result       json.RawMessage
	Error        *string
	NextCheckAt  *time.Time
}

// EventStore holds the append-only data: telemetry, events and inference results.
type EventStore interface {
	// InsertEvents stores events durably. inserted[i] is false when an event with the
	// same (organization, id) already existed: the write is idempotent.
	InsertEvents(ctx context.Context, events []*envelope.Event) (inserted []bool, err error)
	QueryEvents(ctx context.Context, q Query) (events []*envelope.Event, next *Cursor, err error)
	Aggregate(ctx context.Context, q AggregateQuery) ([]Bucket, error)
	DeleteEventsBefore(ctx context.Context, before time.Time) (int64, error)
	Ping(ctx context.Context) error
	Close()
}

// Store is everything the data plane persists.
type Store interface {
	EventStore
	AddDeadLetter(ctx context.Context, d DeadLetter) error

	// CreateCommand stores a command. With an idempotency key that already exists in
	// the organization it returns the existing command and created=false.
	CreateCommand(ctx context.Context, c *Command) (cmd *Command, created bool, err error)
	GetCommand(ctx context.Context, organizationID, id string) (*Command, error)
	// UpdateCommand applies u if the command is in one of u.From; changed=false means
	// it was in another state and the current command is returned untouched.
	UpdateCommand(ctx context.Context, organizationID, id string, u CommandUpdate) (cmd *Command, changed bool, err error)
	// ClaimDueCommands returns non-terminal commands whose NextCheckAt has passed and
	// pushes their NextCheckAt forward by lease so other replicas skip them.
	ClaimDueCommands(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]*Command, error)
	// UndeliveredCommands returns PENDING and DISPATCHED commands for a target that
	// just connected.
	UndeliveredCommands(ctx context.Context, organizationID, applicationID, deviceID string, limit int) ([]*Command, error)
}

// Split keeps events in one store and everything else (commands, dead letters) in
// another: ClickHouse for the data, PostgreSQL for the state that changes.
type Split struct {
	Store             // commands and dead letters
	Events EventStore // telemetry, events, inference
}

func (s *Split) InsertEvents(ctx context.Context, events []*envelope.Event) ([]bool, error) {
	return s.Events.InsertEvents(ctx, events)
}

func (s *Split) QueryEvents(ctx context.Context, q Query) ([]*envelope.Event, *Cursor, error) {
	return s.Events.QueryEvents(ctx, q)
}

func (s *Split) Aggregate(ctx context.Context, q AggregateQuery) ([]Bucket, error) {
	return s.Events.Aggregate(ctx, q)
}

func (s *Split) DeleteEventsBefore(ctx context.Context, before time.Time) (int64, error) {
	return s.Events.DeleteEventsBefore(ctx, before)
}

func (s *Split) Ping(ctx context.Context) error {
	if err := s.Events.Ping(ctx); err != nil {
		return err
	}
	return s.Store.Ping(ctx)
}

func (s *Split) Close() {
	s.Events.Close()
	s.Store.Close()
}
