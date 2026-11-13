// Package state holds the engine's durable records (evaluation state, device cursors,
// timers and the action outbox) and the Store interface that persists them.
//
// Every record that changes the outcome of a rule is written before the triggering
// event is acknowledged, so a restarted worker resumes exactly where the last commit
// left off. Concurrency between workers is optimistic: each record carries a version
// and a commit fails with ErrConflict when someone else wrote it first.
package state

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"meteorcloud/dataplane/internal/rules/eval"
)

// ErrConflict means a record changed since it was loaded. Reload and re-evaluate.
var ErrConflict = errors.New("rule state changed concurrently")

// Alert phases kept by the engine. Acknowledged and silenced are control-plane
// overlays on an alert record, not engine phases.
const (
	PhaseNormal   = "normal"
	PhasePending  = "pending"
	PhaseFiring   = "firing"
	PhaseCooldown = "cooldown"
)

// Key addresses one rule on one device.
type Key struct {
	RuleID   string
	DeviceID string
}

// RuleState is the evaluation state of one rule for one device.
type RuleState struct {
	RuleID        string             `json:"rule_id"`
	DeviceID      string             `json:"device_id"`
	OrgID         string             `json:"organization_id"`
	RuleVersion   int                `json:"rule_version"`
	ConditionHash string             `json:"condition_hash"`
	Phase         string             `json:"phase"`
	PhaseSince    time.Time          `json:"phase_since"`
	Episode       int64              `json:"episode"`
	ActiveAlerts  int                `json:"active_alerts,omitempty"`
	CooldownUntil time.Time          `json:"cooldown_until,omitempty"`
	ActivatedAt   time.Time          `json:"activated_at"`
	LastEvaluated time.Time          `json:"last_evaluated"`
	LastEventID   string             `json:"last_event_id,omitempty"`
	LastValues    map[string]float64 `json:"last_values,omitempty"`
	Nodes         eval.State         `json:"nodes"`
	// TimerDue is the due time currently stored for this rule and device (zero: none).
	TimerDue time.Time `json:"timer_due,omitempty"`
	// Version is the optimistic-lock version: 0 for a record never stored.
	Version int64 `json:"version"`
}

func (s *RuleState) Key() Key { return Key{s.RuleID, s.DeviceID} }

// Clone deep-copies a state (JSON round trip keeps it honest with what is stored).
func (s *RuleState) Clone() *RuleState {
	b, _ := json.Marshal(s)
	var c RuleState
	_ = json.Unmarshal(b, &c)
	if c.Nodes == nil {
		c.Nodes = eval.State{}
	}
	return &c
}

// RecentIDs is how many event ids per device are kept for duplicate detection.
const RecentIDs = 64

// Cursor is per-device progress shared by every rule: the event-time watermark and
// the most recent event ids, for out-of-order and duplicate handling.
type Cursor struct {
	DeviceID  string    `json:"device_id"`
	OrgID     string    `json:"organization_id"`
	Watermark time.Time `json:"watermark"`
	LastSeen  time.Time `json:"last_seen"`
	Recent    []string  `json:"recent"`
	Version   int64     `json:"version"`
}

// Seen reports whether an event id was already processed.
func (c *Cursor) Seen(id string) bool {
	for _, r := range c.Recent {
		if r == id {
			return true
		}
	}
	return false
}

// Remember records an event id, keeping the last RecentIDs.
func (c *Cursor) Remember(id string) {
	c.Recent = append(c.Recent, id)
	if len(c.Recent) > RecentIDs {
		c.Recent = append([]string(nil), c.Recent[len(c.Recent)-RecentIDs:]...)
	}
}

// Timer asks for a re-evaluation of one rule on one device at Due.
type Timer struct {
	RuleID   string    `json:"rule_id"`
	DeviceID string    `json:"device_id"`
	OrgID    string    `json:"organization_id"`
	Due      time.Time `json:"due"`
}

// Outbox statuses.
const (
	OutboxPending   = "pending"
	OutboxDelivered = "delivered"
	OutboxDead      = "dead"
)

// OutboxItem is one action to perform, committed together with the state change
// that produced it. Key is unique: a replayed transition cannot enqueue twice.
type OutboxItem struct {
	ID          int64           `json:"id"`
	Key         string          `json:"idempotency_key"`
	OrgID       string          `json:"organization_id"`
	RuleID      string          `json:"rule_id"`
	RuleVersion int             `json:"rule_version"`
	DeviceID    string          `json:"device_id"`
	Transition  string          `json:"transition"`
	ActionType  string          `json:"action_type"`
	Payload     json.RawMessage `json:"payload"`
	Status      string          `json:"status"`
	Attempts    int             `json:"attempts"`
	NextAttempt time.Time       `json:"next_attempt_at"`
	LastError   string          `json:"last_error,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
}

// Batch is everything one evaluation changes, committed atomically.
type Batch struct {
	Cursor      *Cursor
	States      []*RuleState // inserted (Version 0) or updated (compare-and-swap)
	Deleted     []*RuleState // removed (compare-and-swap)
	TimersSet   []Timer
	TimersClear []Key
	Outbox      []OutboxItem
}

// Empty reports whether the batch writes nothing.
func (b *Batch) Empty() bool {
	return b.Cursor == nil && len(b.States) == 0 && len(b.Deleted) == 0 &&
		len(b.TimersSet) == 0 && len(b.TimersClear) == 0 && len(b.Outbox) == 0
}

// Store persists engine records.
type Store interface {
	// LoadCursor returns the device cursor, or nil when the device is new.
	LoadCursor(ctx context.Context, deviceID string) (*Cursor, error)
	// LoadStates returns the stored states of the given rules for a device; missing
	// rules are absent from the map.
	LoadStates(ctx context.Context, deviceID string, ruleIDs []string) (map[string]*RuleState, error)
	// CommitMany commits each batch atomically and independently (group commit): the
	// error at index i belongs to batch i only. On success the versions inside each
	// batch are advanced to the stored values.
	CommitMany(ctx context.Context, batches []*Batch) []error
	// ClaimTimers returns up to limit timers due at or before now and pushes their due
	// time forward by lease, so another worker does not claim them meanwhile. A timer
	// that is not cleared or rescheduled fires again after the lease.
	ClaimTimers(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]Timer, error)
	// StatesForRule pages through the states of one rule (status API, reconciling a
	// changed or removed rule). Pass the last device id seen as after.
	StatesForRule(ctx context.Context, ruleID, after string, limit int) ([]*RuleState, error)
	// Counts returns the number of stored timers and pending outbox items.
	Counts(ctx context.Context) (timers, outbox int, err error)
	Ping(ctx context.Context) error
	Close()
}
