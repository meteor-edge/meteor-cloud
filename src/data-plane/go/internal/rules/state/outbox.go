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
// hands out an action while an older pending action of the same rule, device and
// type exists, so a resolve cannot overtake its firing.
type Outbox interface {
	ClaimOutbox(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]OutboxItem, error)
	MarkDelivered(ctx context.Context, id int64, now time.Time) error
	Reschedule(ctx context.Context, id int64, next time.Time, lastError string) error
	MarkDead(ctx context.Context, item OutboxItem, lastError string, now time.Time) error
}

const claimSQL = `
	UPDATE rules.action_outbox o SET next_attempt_at = $2, attempts = o.attempts + 1, updated_at = $1
	FROM (
		SELECT c.id FROM rules.action_outbox c
		WHERE c.status = 'pending' AND c.next_attempt_at <= $1
		  AND NOT EXISTS (SELECT 1 FROM rules.action_outbox p WHERE p.status = 'pending'
		      AND p.rule_id = c.rule_id AND p.device_id = c.device_id AND p.action_type = c.action_type AND p.id < c.id)
		ORDER BY c.next_attempt_at, c.id
		LIMIT $3
		FOR UPDATE SKIP LOCKED
	) due
	WHERE o.id = due.id
	RETURNING o.id, o.idempotency_key, o.organization_id, o.rule_id, o.rule_version, o.device_id, o.transition,
		o.action_type, o.payload, o.status, o.attempts, o.next_attempt_at, COALESCE(o.last_error, ''), o.created_at`

func (p *Postgres) ClaimOutbox(ctx context.Context, now time.Time, lease time.Duration, limit int) ([]OutboxItem, error) {
	rows, err := p.pool.Query(ctx, claimSQL, now, now.Add(lease), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []OutboxItem
	for rows.Next() {
		var o OutboxItem
		var payload []byte
		if err := rows.Scan(&o.ID, &o.Key, &o.OrgID, &o.RuleID, &o.RuleVersion, &o.DeviceID, &o.Transition,
			&o.ActionType, &payload, &o.Status, &o.Attempts, &o.NextAttempt, &o.LastError, &o.CreatedAt); err != nil {
			return nil, err
		}
		o.Payload = payload
		out = append(out, o)
	}
	return out, rows.Err()
}

func (p *Postgres) MarkDelivered(ctx context.Context, id int64, now time.Time) error {
	_, err := p.pool.Exec(ctx, `UPDATE rules.action_outbox SET status = 'delivered', last_error = NULL, updated_at = $2
		WHERE id = $1`, id, now)
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
	m.mu.Lock()
	defer m.mu.Unlock()
	blocked := map[[3]string]bool{}
	var out []OutboxItem
	for i := range m.outbox {
		o := &m.outbox[i]
		if o.Status != OutboxPending {
			continue
		}
		k := [3]string{o.RuleID, o.DeviceID, o.ActionType}
		if blocked[k] {
			continue
		}
		blocked[k] = true
		if o.NextAttempt.After(now) || len(out) >= limit {
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

func (m *Memory) MarkDelivered(_ context.Context, id int64, _ time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if o := m.find(id); o != nil {
		o.Status, o.LastError = OutboxDelivered, ""
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
