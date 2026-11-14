package live

import (
	"encoding/json"
	"fmt"
	"sync"
	"testing"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

type sink struct {
	mu   sync.Mutex
	got  []string
	cmds []string
}

func (s *sink) SendEvent(sub string, ev *envelope.Event) {
	s.mu.Lock()
	s.got = append(s.got, sub+":"+ev.ID)
	s.mu.Unlock()
}
func (s *sink) SendCommand(c *storage.Command) {
	s.mu.Lock()
	s.cmds = append(s.cmds, c.ID)
	s.mu.Unlock()
}

func id(org, app, dev string, subscribe []string, commands bool) *identity.Identity {
	return &identity.Identity{OrganizationID: org, ApplicationID: app, Application: app, DeviceID: dev,
		Permissions: identity.Permissions{Subscribe: subscribe, ReceiveCommands: commands}}
}

func ev(org, eid, stream, app, dev string) *envelope.Event {
	return &envelope.Event{ID: eid, OrganizationID: org, Stream: stream, Kind: "telemetry", Application: app,
		DeviceID: dev, Data: json.RawMessage(`{}`)}
}

func TestSubscriptionsAreScopedToPermissionsAndOrganization(t *testing.T) {
	h := NewHub(observability.NewMetrics())
	a, b := &sink{}, &sink{}
	ca := h.Register(id("org-a", "detector", "d1", []string{"energy.*"}, false), a)
	cb := h.Register(id("org-b", "detector", "d1", []string{"energy.*"}, false), b)

	if err := ca.Subscribe(Subscription{ID: "s1", Pattern: "energy.power"}); err != nil {
		t.Fatal(err)
	}
	if err := ca.Subscribe(Subscription{ID: "s2", Pattern: "energy.*", DeviceID: "d2"}); err != nil {
		t.Fatal(err)
	}
	if err := cb.Subscribe(Subscription{ID: "s1", Pattern: "energy.*"}); err != nil {
		t.Fatal(err)
	}
	for pattern, want := range map[string]error{
		"water.flow": ErrForbidden, "energy.**": ErrForbidden, "*.power": ErrForbidden,
		"energy.#": ErrInvalidPattern, "**": ErrInvalidPattern,
	} {
		if err := ca.Subscribe(Subscription{ID: "x-" + pattern, Pattern: pattern}); err != want {
			t.Errorf("%s: want %v, got %v", pattern, want, err)
		}
	}
	if err := ca.Subscribe(Subscription{ID: "s1", Pattern: "energy.power"}); err != ErrDuplicateSubscribe {
		t.Errorf("duplicate id: %v", err)
	}

	h.OnLive(ev("org-a", "e1", "energy.power", "collector", "d1"))
	h.OnLive(ev("org-a", "e2", "energy.voltage", "collector", "d2"))
	h.OnLive(ev("org-a", "e3", "water.flow", "collector", "d1"))
	h.OnLive(ev("org-b", "e4", "energy.power", "collector", "d1"))

	if fmt.Sprint(a.got) != "[s1:e1 s2:e2]" {
		t.Fatalf("org-a deliveries: %v", a.got)
	}
	if fmt.Sprint(b.got) != "[s1:e4]" {
		t.Fatalf("org-b must only see its own events: %v", b.got)
	}

	ca.Unsubscribe("s1")
	ca.Close()
	h.OnLive(ev("org-a", "e5", "energy.power", "collector", "d1"))
	if len(a.got) != 2 {
		t.Fatal("closed connection still receives events")
	}
}

func TestSubscriptionLimit(t *testing.T) {
	h := NewHub(observability.NewMetrics())
	c := h.Register(id("org", "app", "", []string{"energy.*"}, false), &sink{})
	for i := 0; i < MaxSubscriptions; i++ {
		if err := c.Subscribe(Subscription{ID: fmt.Sprint(i), Pattern: "energy.power"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := c.Subscribe(Subscription{ID: "one-more", Pattern: "energy.power"}); err != ErrTooMany {
		t.Fatalf("want ErrTooMany, got %v", err)
	}
}

func TestCommandsReachOnlyTheTarget(t *testing.T) {
	h := NewHub(observability.NewMetrics())
	target1, target2, noPerm, otherApp, otherOrg := &sink{}, &sink{}, &sink{}, &sink{}, &sink{}
	h.Register(id("org", "detector", "d1", nil, true), target1)
	h.Register(id("org", "detector", "d2", nil, true), target2)
	h.Register(id("org", "detector", "d1", nil, false), noPerm)
	h.Register(id("org", "collector", "d1", nil, true), otherApp)
	h.Register(id("other", "detector", "d1", nil, true), otherOrg)

	h.OnCommand(&storage.Command{ID: "c1", OrganizationID: "org", ApplicationID: "detector", DeviceID: "d1"})
	h.OnCommand(&storage.Command{ID: "c2", OrganizationID: "org", ApplicationID: "detector"})

	if fmt.Sprint(target1.cmds) != "[c1 c2]" || fmt.Sprint(target2.cmds) != "[c2]" {
		t.Fatalf("targets: %v %v", target1.cmds, target2.cmds)
	}
	if len(noPerm.cmds)+len(otherApp.cmds)+len(otherOrg.cmds) != 0 {
		t.Fatal("command leaked to a connection that must not receive it")
	}
}
