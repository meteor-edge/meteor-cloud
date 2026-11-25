package eval

import (
	"testing"
	"time"
)

func TestNotIsUnknownBeforeTheFirstObservation(t *testing.T) {
	p := program(t, `{"type":"not","condition":{"type":"comparison","field":"charging","operator":"eq","value":true}}`)
	st := State{}
	if run(p, st, ev(0, `{"v":1}`)).Fire || tick(p, st, time.Second).Fire {
		t.Fatal("fired before charging was ever reported")
	}
	if !run(p, st, ev(2*time.Second, `{"charging":false}`)).Fire || !run(p, st, ev(3*time.Second, `{"v":1}`)).Fire {
		t.Fatal("a reported false fires and is remembered")
	}
	// Unknown under any/all: only a child that settles the outcome counts.
	p = program(t, `{"type":"not","condition":{"type":"all","conditions":[{"type":"comparison","field":"a","operator":"gt","value":1},{"type":"comparison","field":"b","operator":"gt","value":1}]}}`)
	if st = (State{}); run(p, st, ev(0, `{"a":5}`)).Fire {
		t.Fatal("b is unknown")
	}
	if !run(p, st, ev(time.Second, `{"a":0}`)).Fire {
		t.Fatal("a alone makes all false")
	}
}

func TestLateSampleThatClearsTheChildResetsDuration(t *testing.T) {
	const def = `{"type":"duration","duration":"1s","condition":{"type":"aggregate","function":"avg","field":"v","window":"1m","operator":"gt","value":5}}`
	for _, late := range []bool{false, true} {
		p, st := program(t, def), State{}
		run(p, st, ev(0, `{"v":10}`))
		out := run(p, st, ev(2*time.Second, `{"v":10}`))
		if late {
			e := ev(time.Second, `{"v":-20}`)
			out = p.Evaluate(Input{Event: e, Now: ev(2*time.Second, `{}`).Time, OutOfOrder: true, LateTolerance: time.Minute}, st)
		}
		if out.Fire == late {
			t.Fatalf("late %v: fire %v", late, out.Fire)
		}
	}
}

func TestFlattenKeepsEmptySegmentsAndIgnoresArrays(t *testing.T) {
	f, err := FlattenJSON([]byte(`{"":{"v":1},"v":2,"extra":[1e309]}`))
	if err != nil || f["v"].Num != 2 || f[".v"].Num != 1 {
		t.Fatal(err, f)
	}
	for _, bad := range []string{`{"v":1e309}`, `{"v":1} x`, `[1]`, `{"v":`} {
		if _, err := FlattenJSON([]byte(bad)); err == nil {
			t.Error("accepted", bad)
		}
	}
}

func TestRateOfAnOverflowingDifference(t *testing.T) {
	p, st := program(t, `{"type":"aggregate","function":"rate","field":"v","window":"1m","operator":"gt","value":1.9e307}`), State{}
	run(p, st, ev(0, `{"v":-1e308}`))
	if o := run(p, st, ev(10*time.Second, `{"v":1e308}`)); !o.Fire {
		t.Fatal(o.Values)
	}
}

func TestTraceKeepsBothWindowsOfAnAggregate(t *testing.T) {
	p, st := program(t, `{"type":"any","conditions":[{"type":"aggregate","function":"avg","field":"v","window":"1s","operator":"gt","value":80},{"type":"aggregate","function":"avg","field":"v","window":"1m","operator":"gt","value":80}]}`), State{}
	run(p, st, ev(0, `{"v":0}`))
	o := run(p, st, ev(30*time.Second, `{"v":90}`))
	if o.Values["avg(v)"] != 90 || o.Values["avg(v) 1m0s"] != 45 {
		t.Fatal(o.Values)
	}
}
