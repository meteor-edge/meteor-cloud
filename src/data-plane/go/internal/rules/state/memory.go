package state

import (
	"context"
	"encoding/json"
	"sort"
	"sync"
	"time"
)

// Memory is an in-process Store for tests and demos. It copies records in and out,
// so it behaves like a database: a worker that drops its in-memory objects and
// reloads gets exactly what was committed (used by the restart tests).
type Memory struct {
	mu      sync.Mutex
	cursors map[string]*Cursor
	states  map[Key]*RuleState
	timers  map[Key]Timer
	outbox  []OutboxItem
	keys    map[string]bool
	nextID  int64
	// FailCommits makes the next n commits fail (persistence error tests).
	FailCommits int
}

// NewMemory returns an empty store.
func NewMemory() *Memory {
	return &Memory{cursors: map[string]*Cursor{}, states: map[Key]*RuleState{},
		timers: map[Key]Timer{}, keys: map[string]bool{}}
}

type memErr string

func (e memErr) Error() string { return string(e) }

// ErrInjected is returned by commits failed on purpose.
const ErrInjected = memErr("injected commit failure")

func copyCursor(c *Cursor) *Cursor {
	b, _ := json.Marshal(c)
	var out Cursor
	_ = json.Unmarshal(b, &out)
	return &out
}

func (m *Memory) LoadCursor(_ context.Context, deviceID string) (*Cursor, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.cursors[deviceID]
	if !ok {
		return nil, nil
	}
	return copyCursor(c), nil
}

func (m *Memory) LoadStates(_ context.Context, deviceID string, ruleIDs []string) (map[string]*RuleState, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := map[string]*RuleState{}
	for _, id := range ruleIDs {
		if s, ok := m.states[Key{id, deviceID}]; ok {
			out[id] = s.Clone()
		}
	}
	return out, nil
}

func (m *Memory) CommitMany(_ context.Context, batches []*Batch) []error {
	m.mu.Lock()
	defer m.mu.Unlock()
	errs := make([]error, len(batches))
	for i, b := range batches {
		if m.FailCommits > 0 {
			m.FailCommits--
			errs[i] = ErrInjected
			continue
		}
		errs[i] = m.commit(b)
	}
	return errs
}

func (m *Memory) commit(b *Batch) error {
	// Check every precondition first: a batch applies whole or not at all.
	if b.Cursor != nil {
		cur, ok := m.cursors[b.Cursor.DeviceID]
		if (ok && cur.Version != b.Cursor.Version) || (!ok && b.Cursor.Version != 0) {
			return ErrConflict
		}
	}
	check := func(s *RuleState) error {
		cur, ok := m.states[s.Key()]
		if (ok && cur.Version != s.Version) || (!ok && s.Version != 0) {
			return ErrConflict
		}
		return nil
	}
	for _, s := range b.States {
		if err := check(s); err != nil {
			return err
		}
	}
	for _, s := range b.Deleted {
		if _, ok := m.states[s.Key()]; ok {
			if err := check(s); err != nil {
				return err
			}
		}
	}
	if b.Cursor != nil {
		b.Cursor.Version++
		m.cursors[b.Cursor.DeviceID] = copyCursor(b.Cursor)
	}
	for _, s := range b.States {
		s.Version++
		m.states[s.Key()] = s.Clone()
	}
	for _, s := range b.Deleted {
		delete(m.states, s.Key())
		delete(m.timers, s.Key())
	}
	for _, k := range b.TimersClear {
		delete(m.timers, k)
	}
	for _, t := range b.TimersSet {
		m.timers[Key{t.RuleID, t.DeviceID}] = t
	}
	for _, o := range b.Outbox {
		if m.keys[o.Key] {
			continue // idempotent: the transition was already enqueued
		}
		m.keys[o.Key] = true
		m.nextID++
		o.ID = m.nextID
		if o.Status == "" {
			o.Status = OutboxPending
		}
		m.outbox = append(m.outbox, o)
	}
	return nil
}

func (m *Memory) ClaimTimers(_ context.Context, now time.Time, lease time.Duration, limit int) ([]Timer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var due []Timer
	for _, t := range m.timers {
		if !t.Due.After(now) {
			due = append(due, t)
		}
	}
	sort.Slice(due, func(i, j int) bool {
		if due[i].Due.Equal(due[j].Due) {
			return due[i].DeviceID+due[i].RuleID < due[j].DeviceID+due[j].RuleID
		}
		return due[i].Due.Before(due[j].Due)
	})
	if len(due) > limit {
		due = due[:limit]
	}
	for _, t := range due {
		leased := t
		leased.Due = now.Add(lease)
		m.timers[Key{t.RuleID, t.DeviceID}] = leased
	}
	return due, nil
}

func (m *Memory) StatesForRule(_ context.Context, ruleID, after string, limit int) ([]*RuleState, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*RuleState
	for k, s := range m.states {
		if k.RuleID == ruleID && k.DeviceID > after {
			out = append(out, s.Clone())
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].DeviceID < out[j].DeviceID })
	if len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (m *Memory) Counts(context.Context) (int, int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	pending := 0
	for _, o := range m.outbox {
		if o.Status == OutboxPending {
			pending++
		}
	}
	return len(m.timers), pending, nil
}

func (m *Memory) Ping(context.Context) error { return nil }
func (m *Memory) Close()                     {}

// Outbox returns a copy of every enqueued action (tests).
func (m *Memory) Outbox() []OutboxItem {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]OutboxItem(nil), m.outbox...)
}

// Timers returns a copy of every stored timer (tests).
func (m *Memory) Timers() []Timer {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Timer, 0, len(m.timers))
	for _, t := range m.timers {
		out = append(out, t)
	}
	return out
}
