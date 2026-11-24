package state

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func outboxItem(key, rule, device, typ string, at time.Time) OutboxItem {
	return OutboxItem{Key: key, OrgID: "org", RuleID: rule, RuleVersion: 1, DeviceID: device, Transition: "firing",
		ActionType: typ, Payload: json.RawMessage(`{"rule_id":"` + rule + `"}`), NextAttempt: at, CreatedAt: at}
}

func TestOutboxContract(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			ob := s.(Outbox)
			now := time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)
			if errs := s.CommitMany(ctx, []*Batch{{Outbox: []OutboxItem{
				outboxItem("r1:d1:1:firing:a0", "r1", "d1", "alert", now),
				outboxItem("r1:d1:1:resolved:a0", "r1", "d1", "alert", now),
				outboxItem("r1:d1:1:firing:a1", "r1", "d1", "webhook", now),
				outboxItem("r2:d1:1:firing:a0", "r2", "d1", "alert", now.Add(time.Minute)),
			}}}); errs[0] != nil {
				t.Fatal(errs[0])
			}
			got, err := ob.ClaimOutbox(ctx, now, time.Minute, 10)
			if err != nil {
				t.Fatal(err)
			}
			keys := map[string]int{}
			for _, o := range got {
				keys[o.Key] = o.Attempts
			}
			// The resolve waits for its firing; r2 is not due; the webhook is its own key.
			if len(got) != 2 || keys["r1:d1:1:firing:a0"] != 1 || keys["r1:d1:1:firing:a1"] != 1 {
				t.Fatalf("claimed %v", keys)
			}
			if again, _ := ob.ClaimOutbox(ctx, now, time.Minute, 10); len(again) != 0 {
				t.Fatalf("leased items are hidden: %v", again)
			}
			var firing, hook OutboxItem
			for _, o := range got {
				if o.ActionType == "alert" {
					firing = o
				} else {
					hook = o
				}
			}
			if err := ob.Reschedule(ctx, hook.ID, now.Add(30*time.Second), "HTTP 503"); err != nil {
				t.Fatal(err)
			}
			if err := ob.MarkDelivered(ctx, firing.ID, now); err != nil {
				t.Fatal(err)
			}
			later, _ := ob.ClaimOutbox(ctx, now.Add(time.Minute), time.Minute, 10)
			keys = map[string]int{}
			for _, o := range later {
				keys[o.Key] = o.Attempts
			}
			if len(later) != 3 || keys["r1:d1:1:resolved:a0"] != 1 || keys["r1:d1:1:firing:a1"] != 2 || keys["r2:d1:1:firing:a0"] != 1 {
				t.Fatalf("after delivery the resolve is due, the retry is due again: %v", keys)
			}
			for _, o := range later {
				if o.Key == "r1:d1:1:firing:a1" {
					if o.LastError != "HTTP 503" {
						t.Fatalf("last error kept: %q", o.LastError)
					}
					if err := ob.MarkDead(ctx, o, "gave up", now); err != nil {
						t.Fatal(err)
					}
				}
			}
			if p, ok := s.(*Postgres); ok {
				dead, err := p.DeadLetters(ctx, 10)
				if err != nil || len(dead) != 1 || dead[0].Key != "r1:d1:1:firing:a1" || dead[0].Attempts != 2 || dead[0].LastError != "gave up" {
					t.Fatalf("dead letters: %+v %v", dead, err)
				}
			}
		})
	}
}

func TestOutboxOrderIsPerTarget(t *testing.T) {
	for name, s := range stores(t) {
		t.Run(name, func(t *testing.T) {
			ctx, now := context.Background(), time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)
			var items []OutboxItem
			for _, k := range []string{"email:1", "chat:1", "email:2", "chat:2"} {
				o := outboxItem("t:"+k, "rt", "dt", "notification", now)
				o.Target = k[:len(k)-2]
				items = append(items, o)
			}
			if errs := s.CommitMany(ctx, []*Batch{{Outbox: items}}); errs[0] != nil {
				t.Fatal(errs[0])
			}
			got, err := s.(Outbox).ClaimOutbox(ctx, now, time.Minute, 10)
			if err != nil || len(got) != 2 || got[0].Target == got[1].Target {
				t.Fatalf("the first of each target: %+v %v", got, err)
			}
			if err := s.(Outbox).MarkDelivered(ctx, got[0].ID, now); err != nil {
				t.Fatal(err)
			}
			next, _ := s.(Outbox).ClaimOutbox(ctx, now, time.Minute, 10)
			if len(next) != 1 || next[0].Target != got[0].Target {
				t.Fatalf("only the delivered target moves on: %+v", next)
			}
		})
	}
}
