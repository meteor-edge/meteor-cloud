package storage

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"sort"
	"sync"
	"time"

	"meteorcloud/dataplane/internal/envelope"
)

// Memory is an in-process Store for tests and local development. Nothing survives a
// restart; it is never the default.
type Memory struct {
	mu       sync.Mutex
	events   map[string]*envelope.Event // org + "\x00" + id
	commands map[string]*Command
	byKey    map[string]string // org, issuer, idempotency key -> command id
	Dead     []DeadLetter
	// FailInserts makes InsertEvents fail n times (tests of retry and backpressure).
	FailInserts int
}

func NewMemory() *Memory {
	return &Memory{events: map[string]*envelope.Event{}, commands: map[string]*Command{}, byKey: map[string]string{}}
}

func (m *Memory) InsertEvents(_ context.Context, events []*envelope.Event) ([]bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.FailInserts > 0 {
		m.FailInserts--
		return nil, context.DeadlineExceeded
	}
	inserted := make([]bool, len(events))
	for i, ev := range events {
		key := ev.OrganizationID + "\x00" + ev.ID
		if _, ok := m.events[key]; ok {
			continue
		}
		copied := *ev
		m.events[key] = &copied
		inserted[i] = true
	}
	return inserted, nil
}

func matches(ev *envelope.Event, q *Query) bool {
	if ev.OrganizationID != q.OrganizationID || ev.Stream != q.Stream {
		return false
	}
	if q.Kind != "" && ev.Kind != q.Kind {
		return false
	}
	if q.DeviceID != "" && ev.DeviceID != q.DeviceID {
		return false
	}
	if q.Application != "" && ev.Application != q.Application {
		return false
	}
	if !q.Start.IsZero() && ev.Time.Before(q.Start) {
		return false
	}
	if !q.End.IsZero() && !ev.Time.Before(q.End) {
		return false
	}
	if len(q.Filter) > 0 {
		var data map[string]json.RawMessage
		if json.Unmarshal(ev.Data, &data) != nil {
			return false
		}
		for k, want := range q.Filter {
			got, ok := data[k]
			if !ok || !jsonEqual(got, want) {
				return false
			}
		}
	}
	return true
}

func jsonEqual(a, b json.RawMessage) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	xa, _ := json.Marshal(x)
	ya, _ := json.Marshal(y)
	return bytes.Equal(xa, ya)
}

func less(a, b *envelope.Event) bool {
	if !a.Time.Equal(b.Time) {
		return a.Time.Before(b.Time)
	}
	return a.ID < b.ID
}

func (m *Memory) selected(q *Query) []*envelope.Event {
	var out []*envelope.Event
	for _, ev := range m.events {
		if matches(ev, q) {
			out = append(out, ev)
		}
	}
	sort.Slice(out, func(i, j int) bool { return less(out[i], out[j]) })
	return out
}

func (m *Memory) QueryEvents(_ context.Context, q Query) ([]*envelope.Event, *Cursor, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	all := m.selected(&q)
	if q.Descending {
		for i, j := 0, len(all)-1; i < j; i, j = i+1, j-1 {
			all[i], all[j] = all[j], all[i]
		}
	}
	var out []*envelope.Event
	for _, ev := range all {
		if q.After != nil {
			pos := &envelope.Event{Time: q.After.Time, ID: q.After.ID}
			if q.Descending && !less(ev, pos) || !q.Descending && !less(pos, ev) {
				continue
			}
		}
		copied := *ev
		out = append(out, &copied)
		if len(out) == q.Limit+1 {
			break
		}
	}
	var next *Cursor
	if q.Limit > 0 && len(out) > q.Limit {
		out = out[:q.Limit]
		last := out[len(out)-1]
		next = &Cursor{Time: last.Time, ID: last.ID}
	}
	return out, next, nil
}

func numberAt(data json.RawMessage, path []string) (float64, bool) {
	var cur any
	if json.Unmarshal(data, &cur) != nil {
		return 0, false
	}
	for _, p := range path {
		obj, ok := cur.(map[string]any)
		if !ok {
			return 0, false
		}
		cur, ok = obj[p]
		if !ok {
			return 0, false
		}
	}
	v, ok := cur.(float64)
	return v, ok
}

func (m *Memory) Aggregate(_ context.Context, q AggregateQuery) ([]Bucket, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	type acc struct {
		sum, min, max float64
		n, rows       int64
	}
	buckets := map[int64]*acc{}
	for _, ev := range m.selected(&q.Query) {
		start := ev.Time.UnixNano() / int64(q.Bucket) * int64(q.Bucket)
		a := buckets[start]
		if a == nil {
			a = &acc{min: math.Inf(1), max: math.Inf(-1)}
			buckets[start] = a
		}
		a.rows++
		if v, ok := numberAt(ev.Data, q.Field); ok {
			a.n++
			a.sum += v
			a.min = math.Min(a.min, v)
			a.max = math.Max(a.max, v)
		}
	}
	keys := make([]int64, 0, len(buckets))
	for k := range buckets {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	out := make([]Bucket, 0, len(keys))
	for _, k := range keys {
		a := buckets[k]
		b := Bucket{Start: time.Unix(0, k).UTC(), Count: a.n}
		if q.Func == "count" {
			b.Count = a.rows
			v := float64(a.rows)
			b.Value = &v
		} else if a.n > 0 {
			var v float64
			switch q.Func {
			case "avg":
				v = a.sum / float64(a.n)
			case "sum":
				v = a.sum
			case "min":
				v = a.min
			case "max":
				v = a.max
			}
			b.Value = &v
		}
		out = append(out, b)
	}
	return out, nil
}

func (m *Memory) DeleteEventsBefore(_ context.Context, before time.Time) (int64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var n int64
	for k, ev := range m.events {
		if ev.Time.Before(before) {
			delete(m.events, k)
			n++
		}
	}
	return n, nil
}

func (m *Memory) AddDeadLetter(_ context.Context, d DeadLetter) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Dead = append(m.Dead, d)
	return nil
}

// DeadLetters returns a copy of the stored dead letters (tests).
func (m *Memory) DeadLetters() []DeadLetter {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]DeadLetter(nil), m.Dead...)
}

func cloneCommand(c *Command) *Command {
	copied := *c
	return &copied
}

func (m *Memory) CreateCommand(_ context.Context, c *Command) (*Command, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if c.IdempotencyKey != "" {
		key := c.OrganizationID + "\x00" + c.IssuedBy + "\x00" + c.IdempotencyKey
		if id, ok := m.byKey[key]; ok {
			return cloneCommand(m.commands[id]), false, nil
		}
		m.byKey[key] = c.ID
	}
	m.commands[c.ID] = cloneCommand(c)
	return cloneCommand(c), true, nil
}

func (m *Memory) GetCommand(_ context.Context, organizationID, id string) (*Command, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.commands[id]
	if !ok || c.OrganizationID != organizationID {
		return nil, ErrNotFound
	}
	return cloneCommand(c), nil
}

func (m *Memory) UpdateCommand(_ context.Context, organizationID, id string, u CommandUpdate) (*Command, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.commands[id]
	if !ok || c.OrganizationID != organizationID {
		return nil, false, ErrNotFound
	}
	allowed := false
	for _, s := range u.From {
		if c.State == s {
			allowed = true
		}
	}
	if !allowed {
		return cloneCommand(c), false, nil
	}
	if u.State != "" {
		c.State = u.State
	}
	if u.IncAttempts {
		c.Attempts++
	}
	if u.DispatchedAt != nil {
		c.DispatchedAt = u.DispatchedAt
	}
	if u.ReceivedAt != nil && c.ReceivedAt == nil {
		c.ReceivedAt = u.ReceivedAt
	}
	if u.CompletedAt != nil {
		c.CompletedAt = u.CompletedAt
	}
	if u.Result != nil {
		c.Result = u.Result
	}
	if u.Error != nil {
		c.Error = *u.Error
	}
	if u.NextCheckAt != nil {
		c.NextCheckAt = *u.NextCheckAt
	}
	c.UpdatedAt = time.Now().UTC()
	return cloneCommand(c), true, nil
}

func (m *Memory) ClaimDueCommands(_ context.Context, now time.Time, lease time.Duration, limit int) ([]*Command, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*Command
	for _, c := range m.commands {
		if Terminal(c.State) || c.NextCheckAt.After(now) {
			continue
		}
		c.NextCheckAt = now.Add(lease)
		out = append(out, cloneCommand(c))
		if len(out) == limit {
			break
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.Before(out[j].CreatedAt) })
	return out, nil
}

func (m *Memory) UndeliveredCommands(_ context.Context, organizationID, applicationID, deviceID string, limit int) ([]*Command, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*Command
	for _, c := range m.commands {
		if c.OrganizationID != organizationID || c.ApplicationID != applicationID {
			continue
		}
		if c.State != StatePending && c.State != StateDispatched {
			continue
		}
		if c.DeviceID != "" && c.DeviceID != deviceID {
			continue
		}
		out = append(out, cloneCommand(c))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.Before(out[j].CreatedAt) })
	if len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (m *Memory) Ping(context.Context) error { return nil }
func (m *Memory) Close()                     {}
