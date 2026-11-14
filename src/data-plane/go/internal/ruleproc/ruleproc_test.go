package ruleproc

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/envelope"
)

func TestSlotUsesOrgAndDeviceNotFullSubject(t *testing.T) {
	p := &Processor{}
	a := p.slot("telemetry.v1.org-1.dev-1", 8)
	b := p.slot("device.status.v1.org-1.dev-1", 8)
	c := p.slot("telemetry.v1.org-1.dev-2", 8)
	if a != b {
		t.Fatalf("same org+device must share a lane: %d vs %d", a, b)
	}
	if a == c {
		t.Fatalf("different devices should usually differ (got both %d)", a)
	}
}

func TestDedupeSerializesMatchingKeys(t *testing.T) {
	var calls atomic.Int32
	started := make(chan struct{})
	release := make(chan struct{})
	h := Dedupe(HandlerFunc(func(context.Context, *envelope.Event) error {
		calls.Add(1)
		close(started)
		<-release
		return nil
	}), 100)

	ev := &envelope.Event{OrganizationID: "org", ID: "e1"}
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); _ = h.Handle(context.Background(), ev) }()
	<-started
	go func() { defer wg.Done(); _ = h.Handle(context.Background(), ev) }()
	// Give the second call time to block on the in-flight key.
	time.Sleep(20 * time.Millisecond)
	close(release)
	wg.Wait()
	if calls.Load() != 1 {
		t.Fatalf("want 1 handle call, got %d", calls.Load())
	}
}
