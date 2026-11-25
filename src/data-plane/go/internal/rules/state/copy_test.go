package state

import (
	"math"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/eval"
)

func TestCloneSharesNothing(t *testing.T) {
	last, since := true, t0
	s := newState("r", "d")
	s.LastValues = map[string]float64{"x": 1}
	s.Nodes = eval.State{"c": {Last: &last, Since: &since, Buckets: []eval.Bucket{{N: 1}}}}
	c := s.Clone()
	c.LastValues["x"], *c.Nodes["c"].Last, c.Nodes["c"].Buckets[0].N = 2, false, 9
	*c.Nodes["c"].Since = t0.Add(1)
	if s.LastValues["x"] != 1 || !last || s.Nodes["c"].Buckets[0].N != 1 || !s.Nodes["c"].Since.Equal(t0) {
		t.Fatalf("%+v", s)
	}
}

func TestMemoryRefusesWhatJSONCannotStore(t *testing.T) {
	s := newState("r", "d")
	s.LastValues = map[string]float64{"x": math.Inf(1)}
	if commit(t, NewMemory(), &Batch{States: []*RuleState{s}}) == nil {
		t.Fatal("infinity cannot be stored")
	}
}

func TestMemoryCopiesOutboxPayloads(t *testing.T) {
	m := NewMemory()
	p := []byte(`{"a":1}`)
	if err := commit(t, m, &Batch{Outbox: []OutboxItem{{Key: "k", Payload: p}}}); err != nil {
		t.Fatal(err)
	}
	p[0] = 'x'
	m.Outbox()[0].Payload[1] = 'x'
	if got := string(m.Outbox()[0].Payload); got != `{"a":1}` {
		t.Fatal(got)
	}
}

func TestMemoryRefusesAnUnencodableCursor(t *testing.T) {
	c := &Cursor{DeviceID: "d", Watermark: time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)}
	if commit(t, NewMemory(), &Batch{Cursor: c}) == nil || c.Version != 0 {
		t.Fatalf("version %d", c.Version)
	}
}
