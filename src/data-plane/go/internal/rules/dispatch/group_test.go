package dispatch

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

func offlineItems(n int, typ, transition string, next time.Time) []state.OutboxItem {
	var out []state.OutboxItem
	for i := range n {
		dev := fmt.Sprintf("dev-%03d", i)
		raw, _ := json.Marshal(engine.ActionPayload{RuleID: "r1", RuleName: "Device offline", OrganizationID: "org-1", DeviceID: dev,
			Severity: "critical", Transition: engine.Transition{Kind: transition, Episode: 1, At: t0}, Action: dsl.Action{Type: typ, IntegrationID: "ch"}})
		out = append(out, state.OutboxItem{Key: "r1:" + dev + ":1:" + transition + ":" + typ, OrgID: "org-1", RuleID: "r1", DeviceID: dev,
			Transition: transition, ActionType: typ, Target: "ch", Payload: raw, Status: state.OutboxPending, NextAttempt: next, CreatedAt: t0})
	}
	return out
}

// groupPlatform is a control plane whose channel is of the given kind, plus the chat.
type groupPlatform struct {
	mu     sync.Mutex
	status int
	posts  []string
}

func (g *groupPlatform) sender(t *testing.T, kind string) *WebhookSender {
	t.Helper()
	chat := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		g.mu.Lock()
		defer g.mu.Unlock()
		g.posts = append(g.posts, string(b))
		if g.status != 0 {
			w.WriteHeader(g.status)
		}
	}))
	t.Cleanup(chat.Close)
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Devices []struct {
				DeviceID string `json:"device_id"`
			}
		}
		json.NewDecoder(r.Body).Decode(&body)
		devices := []DeviceDelivery{}
		for _, d := range body.Devices {
			devices = append(devices, DeviceDelivery{Name: "name-" + d.DeviceID, Silenced: d.DeviceID == "dev-003"})
		}
		json.NewEncoder(w).Encode(map[string]any{"url": chat.URL, "kind": kind, "enabled": true, "devices": devices})
	}))
	t.Cleanup(cp.Close)
	return &WebhookSender{Resolver: &ControlPlaneResolver{CP: &ControlPlane{URL: cp.URL}}, Client: chat.Client()}
}

func TestDevicesThatChangeTogetherShareOneMessage(t *testing.T) {
	ctx := context.Background()
	for _, kind := range []string{"slack", "webhook"} {
		t.Run(kind, func(t *testing.T) {
			g := &groupPlatform{}
			store, clock := state.NewMemory(), engine.NewFakeClock(t0)
			d := New(Config{GroupWait: 10 * time.Second}, store, map[string]Sender{dsl.ActionNotification: g.sender(t, kind)}, clock, nil, nil)
			items := append(offlineItems(300, dsl.ActionNotification, "firing", t0), offlineItems(2, dsl.ActionNotification, "resolved", t0)...)
			store.CommitMany(ctx, []*state.Batch{{Outbox: items}})
			for range 100 {
				if n, err := d.Pass(ctx); err != nil || n == 0 {
					break
				}
			}
			want := 2 // one for the 300, then one for the two that came back
			if kind == "webhook" {
				want = 302 // a program gets one request per alert
			}
			if len(g.posts) != want {
				t.Fatalf("%d messages, want %d", len(g.posts), want)
			}
			if kind == "slack" {
				for _, s := range []string{"Device offline on 299 devices", "name-dev-000", "… and 279 more"} {
					if !strings.Contains(g.posts[0], s) {
						t.Fatalf("no %q in %s", s, g.posts[0])
					}
				}
				if strings.Contains(g.posts[0], "name-dev-003") || !strings.Contains(g.posts[1], "RESOLVED") {
					t.Fatalf("silenced device named, or resolve missing: %v", g.posts)
				}
			}
			for _, o := range store.Outbox() {
				if o.Status != state.OutboxDelivered {
					t.Fatalf("%s is %s", o.Key, o.Status)
				}
			}
		})
	}
}

func TestAGroupThatFailsIsRetriedAsOneMessage(t *testing.T) {
	ctx := context.Background()
	g := &groupPlatform{status: http.StatusServiceUnavailable}
	store, clock := state.NewMemory(), engine.NewFakeClock(t0)
	d := New(Config{}, store, map[string]Sender{dsl.ActionNotification: g.sender(t, "slack")}, clock, nil, nil)
	store.CommitMany(ctx, []*state.Batch{{Outbox: offlineItems(50, dsl.ActionNotification, "firing", t0)}})
	d.Pass(ctx)
	g.status = 0
	clock.Advance(6 * time.Second)
	d.Pass(ctx)
	if len(g.posts) != 2 || !strings.Contains(g.posts[1], "49 devices") {
		t.Fatalf("messages %d: %v", len(g.posts), g.posts)
	}
}

type panicGroup struct{}

func (panicGroup) Send(context.Context, state.OutboxItem, *engine.ActionPayload) (Outcome, error) {
	panic("send")
}
func (panicGroup) Groups(context.Context, state.OutboxItem, *engine.ActionPayload) (bool, bool, int) {
	return true, false, 10
}
func (panicGroup) SendGroup(context.Context, []state.OutboxItem, []*engine.ActionPayload) []Result {
	panic("group")
}

func TestAPanickingGroupIsDeadLettered(t *testing.T) {
	ctx := context.Background()
	store := state.NewMemory()
	d := New(Config{}, store, map[string]Sender{dsl.ActionNotification: panicGroup{}}, engine.NewFakeClock(t0), nil, nil)
	store.CommitMany(ctx, []*state.Batch{{Outbox: offlineItems(5, dsl.ActionNotification, "firing", t0)}})
	if _, err := d.Pass(ctx); err != nil {
		t.Fatal(err)
	}
	for _, s := range statuses(store) {
		if s != state.OutboxDead {
			t.Fatalf("statuses %v", statuses(store))
		}
	}
}

func TestOneRefusedAlertDoesNotTakeTheBatchWithIt(t *testing.T) {
	ctx := context.Background()
	requests := 0
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var body struct{ Items []map[string]any }
		json.NewDecoder(r.Body).Decode(&body)
		results := []map[string]string{}
		for _, it := range body.Items {
			if it["device_id"] == "dev-007" {
				w.WriteHeader(http.StatusUnprocessableEntity)
				return
			}
			results = append(results, map[string]string{"status": "applied"})
		}
		json.NewEncoder(w).Encode(map[string]any{"results": results})
	}))
	defer cp.Close()
	store := state.NewMemory()
	d := New(Config{Workers: 1}, store, map[string]Sender{dsl.ActionAlert: &AlertSender{CP: &ControlPlane{URL: cp.URL}}},
		engine.NewFakeClock(t0), nil, nil)
	store.CommitMany(ctx, []*state.Batch{{Outbox: offlineItems(250, dsl.ActionAlert, "firing", t0)}})
	for range 10 {
		d.Pass(ctx)
	}
	for _, o := range store.Outbox() {
		want := state.OutboxDelivered
		if o.DeviceID == "dev-007" {
			want = state.OutboxDead
		}
		if o.Status != want {
			t.Fatalf("%s is %s", o.Key, o.Status)
		}
	}
	if requests > 110 { // 1 + 100 singly for the refused batch, 2 for the rest
		t.Fatalf("%d requests", requests)
	}
}
