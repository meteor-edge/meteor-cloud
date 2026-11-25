package engine

import (
	"context"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
)

const hotDef = `{"schema_version":1,"name":"hot","scope":{"type":"organization"},"condition":{"type":"comparison","field":"t","operator":"gt","value":80}}`

func (h *harness) setDevices(devices []Device) {
	h.t.Helper()
	h.snap = &Snapshot{Version: h.snap.Version + "+", Rules: h.snap.Rules, Devices: devices}
	rs := CompileSnapshot(h.snap, dsl.DefaultLimits())
	h.e.Apply(rs)
	if err := h.e.Reconcile(context.Background(), rs); err != nil {
		h.t.Fatal(err)
	}
}

// Switching a rule off and on again must alert again: the second firing is a new
// episode with its own idempotency key, not a replay of the first one.
func TestReenabledRuleAlertsAgain(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", hotDef))
	for i := 0; i < 3; i++ {
		at := time.Duration(i) * time.Minute
		h.send("dev-a", at, `{"t":90}`)
		h.update()
		h.update(rule("r", "org-1", hotDef))
	}
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonRemoved},
		tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonRemoved},
		tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonRemoved})
}

func TestDeviceLeavingTheScopeResolvesAndCanComeBack(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", `{"schema_version":1,"name":"hot","scope":{"type":"group","group_id":"grp-1"},"condition":{"type":"comparison","field":"t","operator":"gt","value":80}}`))
	h.send("dev-a", 0, `{"t":90}`)
	moved := append([]Device{}, testDevices...)
	moved[0].GroupID = "grp-2"
	h.setDevices(moved)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonNoDevice})
	h.setDevices(testDevices)
	h.send("dev-a", time.Minute, `{"t":95}`)
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonNoDevice}, tr{"dev-a", "firing", ""})
	h.setDevices(testDevices[1:]) // the device was disabled or deleted
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonNoDevice}, tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonNoDevice})
}

func TestRuleRemovedWhileTheWorkerWasDown(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", hotDef))
	h.send("dev-a", 0, `{"t":90}`)
	h.snap = &Snapshot{Version: "later", Devices: testDevices}
	h.restart()
	for i := 0; i < 2; i++ { // reconciling twice resolves once
		if err := h.e.Reconcile(context.Background(), h.e.Rules()); err != nil {
			t.Fatal(err)
		}
	}
	h.expect(tr{"dev-a", "firing", ""}, tr{"dev-a", "resolved", ReasonRemoved})
}

func TestAbsenceCoversDevicesAddedAfterTheRule(t *testing.T) {
	h := newHarness(t, rule("r", "org-1", `{"schema_version":1,"name":"a","scope":{"type":"device","device_ids":["dev-new"]},"condition":{"type":"absence","duration":"5m"}}`))
	h.setDevices(append(append([]Device{}, testDevices...), Device{ID: "dev-new", OrganizationID: "org-1"}))
	h.advance(6 * time.Minute)
	h.expect(tr{"dev-new", "firing", ""})
}
