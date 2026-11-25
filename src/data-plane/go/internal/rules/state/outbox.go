package state

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Outbox is what the action dispatcher needs from a store.
//
// ClaimOutbox hands out pending actions that are due, hiding each for lease (a
// worker that dies mid-delivery loses its claim when the lease runs out). It never
// hands out an action while an older pending action of the same rule, device, type
// and target exists, so a resolve cannot overtake its firing.
type Outbox interface {
	ClaimOutbox(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]OutboxItem, error)
	// ClaimMore claims, next to an already claimed action, others that can be delivered
	// with it: same organization, type, target and, unless anyRule, rule and
	// transition. Besides due ones it takes never-tried ones due until dueBy. The
	// per-device order of ClaimOutbox holds here too.
	ClaimMore(ctx context.Context, leader OutboxItem, anyRule bool, now, dueBy time.Time, lease time.Duration, limit int) ([]OutboxItem, error)
	MarkDelivered(ctx context.Context, now time.Time, ids ...int64) error
	Reschedule(ctx context.Context, id int64, next time.Time, lastError string) error
	MarkDead(ctx context.Context, item OutboxItem, lastError string, now time.Time) error
}

const (
	claimHead = `
	UPDATE rules.action_outbox o SET next_attempt_at = $2, attempts = o.attempts + 1, updated_at = $1
	FROM (
		SELECT c.id FROM rules.action_outbox c
		WHERE c.status = 'pending' AND `
	claimTail = `
		  AND NOT EXISTS (SELECT 1 FROM rules.action_outbox p WHERE p.status = 'pending'
		      AND p.rule_id = c.rule_id AND p.device_id = c.device_id AND p.action_type = c.action_type AND p.target = c.target AND p.id < c.id)
		ORDER BY c.next_attempt_at, c.id
		LIMIT $3
		FOR UPDATE SKIP LOCKED
	) due
	WHERE o.id = due.id
	RETURNING o.id, o.idempotency_key, o.organization_id, o.rule_id, o.rule_version, o.device_id, o.transition,
		o.action_type, o.target, o.payload, o.status, o.attempts, o.next_attempt_at, COALESCE(o.last_error, ''), o.created_at`
	claimSQL     = claimHead + `c.next_attempt_at <= $1` + claimTail
	claimMoreSQL = claimHead + `(c.next_attempt_at <= $1 OR (c.attempts = 0 AND c.next_attempt_at <= $4))
		  AND c.organization_id = $5 AND c.action_type = $6 AND c.target = $7
		  AND ($8 OR (c.rule_id = $9 AND c.transition = $10))` + claimTail
)

func (p *Postgres) ClaimOutbox(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]OutboxItem, error) {
	return p.claim(ctx, claimSQL, now, now.Add(lease), limit)
}

func (p *Postgres) ClaimMore(ctx context.Context, l OutboxItem, anyRule bool, now, dueBy time.Time, lease time.Duration, limit int) ([]OutboxItem, error) {
	return p.claim(ctx, claimMoreSQL, now, now.Add(lease), limit, dueBy, l.OrgID, l.ActionType, l.Target, anyRule, l.RuleID, l.Transition)
}

func (p *Postgres) claim(ctx context.Context, sql string, args ...any) ([]OutboxItem, error) {
	rows, err := p.pool.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []OutboxItem
	for rows.Next() {
		var o OutboxItem
		var payload []byte
		if err := rows.Scan(&o.ID, &o.Key, &o.OrgID, &o.RuleID, &o.RuleVersion, &o.DeviceID, &o.Transition,
			&o.ActionType, &o.Target, &payload, &o.Status, &o.Attempts, &o.NextAttempt, &o.LastError, &o.CreatedAt); err != nil {
			return nil, err
		}
		o.Payload = payload
		out = append(out, o)
	}
	return out, rows.Err()
}

func (p *Postgres) MarkDelivered(ctx context.Context, now time.Time, ids ...int64) error {
	_, err := p.pool.Exec(ctx, `UPDATE rules.action_outbox SET status = 'delivered', last_error = NULL, updated_at = $2
		WHERE id = ANY($1)`, ids, now)
	return err
}

func (p *Postgres) Reschedule(ctx context.Context, id int64, next time.Time, lastError string) error {
	_, err := p.pool.Exec(ctx, `UPDATE rules.action_outbox SET next_attempt_at = $2, last_error = $3, updated_at = now()
		WHERE id = $1 AND status = 'pending'`, id, next, truncate(lastError))
	return err
}

func (p *Postgres) MarkDead(ctx context.Context, o OutboxItem, lastError string, now time.Time) error {
	return pgx.BeginFunc(ctx, p.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `INSERT INTO rules.action_dead_letters
			(id, idempotency_key, organization_id, rule_id, device_id, action_type, payload, attempts, last_error, failed_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (id) DO NOTHING`,
			o.ID, o.Key, o.OrgID, o.RuleID, o.DeviceID, o.ActionType, []byte(o.Payload), o.Attempts, truncate(lastError), now); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, `UPDATE rules.action_outbox SET status = 'dead', last_error = $2, updated_at = $3 WHERE id = $1`,
			o.ID, truncate(lastError), now)
		return err
	})
}

// DeadLetters lists failed actions, newest first (operators and tests).
func (p *Postgres) DeadLetters(ctx context.Context, limit int) ([]OutboxItem, error) {
	rows, err := p.pool.Query(ctx, `SELECT id, idempotency_key, organization_id, rule_id, device_id, action_type, payload,
		attempts, last_error, failed_at FROM rules.action_dead_letters ORDER BY failed_at DESC, id DESC LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []OutboxItem
	for rows.Next() {
		var o OutboxItem
		var payload []byte
		if err := rows.Scan(&o.ID, &o.Key, &o.OrgID, &o.RuleID, &o.DeviceID, &o.ActionType, &payload, &o.Attempts,
			&o.LastError, &o.CreatedAt); err != nil {
			return nil, err
		}
		o.Payload, o.Status = payload, OutboxDead
		out = append(out, o)
	}
	return out, rows.Err()
}

func truncate(s string) string {
	if len(s) > 1000 {
		return s[:1000]
	}
	return s
}

// ---- memory ----

func (m *Memory) ClaimOutbox(_ context.Context, now time.Time, lease time.Duration, limit int) ([]OutboxItem, error) {
	return m.claim(now, lease, limit, func(o *OutboxItem) bool { return !o.NextAttempt.After(now) })
}

func (m *Memory) ClaimMore(_ context.Context, l OutboxItem, anyRule bool, now, dueBy time.Time, lease time.Duration, limit int) ([]OutboxItem, error) {
	return m.claim(now, lease, limit, func(o *OutboxItem) bool {
		return (!o.NextAttempt.After(now) || (o.Attempts == 0 && !o.NextAttempt.After(dueBy))) &&
			o.OrgID == l.OrgID && o.ActionType == l.ActionType && o.Target == l.Target &&
			(anyRule || (o.RuleID == l.RuleID && o.Transition == l.Transition))
	})
}

func (m *Memory) claim(now time.Time, lease time.Duration, limit int, match func(*OutboxItem) bool) ([]OutboxItem, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	blocked := map[[4]string]bool{}
	var out []OutboxItem
	for i := range m.outbox {
		o := &m.outbox[i]
		if o.Status != OutboxPending {
			continue
		}
		k := [4]string{o.RuleID, o.DeviceID, o.ActionType, o.Target}
		if blocked[k] {
			continue
		}
		blocked[k] = true
		if !match(o) || len(out) >= limit {
			continue
		}
		o.NextAttempt = now.Add(lease)
		o.Attempts++
		out = append(out, *o)
	}
	return out, nil
}

func (m *Memory) find(id int64) *OutboxItem {
	for i := range m.outbox {
		if m.outbox[i].ID == id {
			return &m.outbox[i]
		}
	}
	return nil
}

func (m *Memory) MarkDelivered(_ context.Context, _ time.Time, ids ...int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, id := range ids {
		if o := m.find(id); o != nil {
			o.Status, o.LastError = OutboxDelivered, ""
		}
	}
	return nil
}

func (m *Memory) Reschedule(_ context.Context, id int64, next time.Time, lastError string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if o := m.find(id); o != nil && o.Status == OutboxPending {
		o.NextAttempt, o.LastError = next, truncate(lastError)
	}
	return nil
}

func (m *Memory) MarkDead(_ context.Context, item OutboxItem, lastError string, _ time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if o := m.find(item.ID); o != nil {
		o.Status, o.LastError = OutboxDead, truncate(lastError)
	}
	return nil
}
