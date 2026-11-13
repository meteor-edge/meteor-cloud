package eval

import (
	"encoding/json"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
)

var t0 = time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC)

func program(t *testing.T, cond string) *Program {
	t.Helper()
	raw := `{"schema_version":1,"name":"t","scope":{"type":"organization"},"condition":` + cond + `}`
	r, err := dsl.ParseAndValidate([]byte(raw), dsl.DefaultLimits(), false)
	if err != nil {
		t.Fatal(err)
	}
	p, err := Compile(r)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func ev(at time.Duration, fields string) *Event {
	f, err := FlattenJSON([]byte(fields))
	if err != nil {
		panic(err)
	}
	return &Event{ID: "e", Stream: dsl.DefaultStream, Time: t0.Add(at), Fields: f}
}

func run(p *Program, st State, e *Event) Output {
	return p.Evaluate(Input{Event: e, Now: e.Time, LateTolerance: time.Minute, ActivatedAt: t0}, st)
}

func tick(p *Program, st State, at time.Duration) Output {
	return p.Evaluate(Input{Now: t0.Add(at), LateTolerance: time.Minute, ActivatedAt: t0}, st)
}

func TestComparisonOperators(t *testing.T) {
	cases := []struct {
		cond, data string
		want       bool
	}{
		{`{"type":"comparison","field":"v","operator":"eq","value":5}`, `{"v":5}`, true},
		{`{"type":"comparison","field":"v","operator":"eq","value":"on"}`, `{"v":"on"}`, true},
		{`{"type":"comparison","field":"v","operator":"eq","value":true}`, `{"v":false}`, false},
		{`{"type":"comparison","field":"v","operator":"ne","value":5}`, `{"v":6}`, true},
		{`{"type":"comparison","field":"v","operator":"ne","value":5}`, `{"v":"5"}`, true},
		{`{"type":"comparison","field":"v","operator":"gt","value":90}`, `{"v":90.5}`, true},
		{`{"type":"comparison","field":"v","operator":"gt","value":90}`, `{"v":90}`, false},
		{`{"type":"comparison","field":"v","operator":"gte","value":90}`, `{"v":90}`, true},
		{`{"type":"comparison","field":"v","operator":"lt","value":10}`, `{"v":9}`, true},
		{`{"type":"comparison","field":"v","operator":"lte","value":10}`, `{"v":11}`, false},
		{`{"type":"comparison","field":"v","operator":"gt","value":1}`, `{"v":"2"}`, false},
		{`{"type":"comparison","field":"v","operator":"between","min":1,"max":3}`, `{"v":3}`, true},
		{`{"type":"comparison","field":"v","operator":"between","min":1,"max":3}`, `{"v":3.1}`, false},
		{`{"type":"comparison","field":"v","operator":"in","values":["a","b"]}`, `{"v":"b"}`, true},
		{`{"type":"comparison","field":"v","operator":"not_in","values":[1,2]}`, `{"v":3}`, true},
		{`{"type":"comparison","field":"v","operator":"exists"}`, `{"v":null,"w":1}`, false},
		{`{"type":"comparison","field":"net.rx","operator":"exists"}`, `{"net":{"rx":1}}`, true},
		{`{"type":"comparison","field":"v","operator":"not_exists"}`, `{"w":1}`, true},
	}
	for i, c := range cases {
		p := program(t, c.cond)
		if got := run(p, State{}, ev(0, c.data)).Fire; got != c.want {
			t.Errorf("case %d %s on %s: got %v", i, c.cond, c.data, got)
		}
	}
}

func TestLogicalAndNested(t *testing.T) {
	p := program(t, `{"type":"all","conditions":[
		{"type":"comparison","field":"cpu","operator":"gt","value":90},
		{"type":"any","conditions":[
			{"type":"comparison","field":"temp","operator":"gt","value":70},
			{"type":"not","condition":{"type":"comparison","field":"fan","operator":"eq","value":"ok"}}]}]}`)
	cases := []struct {
		data string
		want bool
	}{
		{`{"cpu":95,"temp":80,"fan":"ok"}`, true},
		{`{"cpu":95,"temp":60,"fan":"ok"}`, false},
		{`{"cpu":95,"temp":60,"fan":"broken"}`, true},
		{`{"cpu":50,"temp":80,"fan":"broken"}`, false},
	}
	for _, c := range cases {
		if got := run(p, State{}, ev(0, c.data)).Fire; got != c.want {
			t.Errorf("%s: got %v", c.data, got)
		}
	}
}

func TestMissingFieldKeepsLastOutcome(t *testing.T) {
	p := program(t, `{"type":"all","conditions":[
		{"type":"comparison","field":"cpu","operator":"gt","value":90},
		{"type":"comparison","field":"temp","operator":"gt","value":70}]}`)
	st := State{}
	if run(p, st, ev(0, `{"cpu":95}`)).Fire {
		t.Fatal("temp unknown yet")
	}
	if !run(p, st, ev(time.Second, `{"temp":75}`)).Fire {
		t.Fatal("cpu remembered from the earlier message")
	}
}

func TestDurationPendingThenFiresAndResets(t *testing.T) {
	p := program(t, `{"type":"duration","duration":"5m","condition":{"type":"comparison","field":"mem","operator":"gt","value":90}}`)
	st := State{}
	o := run(p, st, ev(0, `{"mem":95}`))
	if o.Fire || !o.Pending || !o.Deadline.Equal(t0.Add(5*time.Minute)) {
		t.Fatalf("pending with deadline: %+v", o)
	}
	if o = run(p, st, ev(4*time.Minute, `{"mem":96}`)); o.Fire {
		t.Fatal("not yet")
	}
	if o = run(p, st, ev(5*time.Minute, `{"mem":97}`)); !o.Fire {
		t.Fatal("held for 5 minutes")
	}
	if o = run(p, st, ev(6*time.Minute, `{"mem":50}`)); o.Fire || o.Pending {
		t.Fatal("reset")
	}
	run(p, st, ev(7*time.Minute, `{"mem":95}`))
	if o = run(p, st, ev(11*time.Minute, `{"mem":95}`)); o.Fire {
		t.Fatal("restarted the 5 minutes at minute 7")
	}
	// A quiet device still fires on the timer.
	if o = tick(p, st, 12*time.Minute); !o.Fire {
		t.Fatal("tick at minute 12 fires")
	}
}

func TestRemainsFalseForDuration(t *testing.T) {
	p := program(t, `{"type":"duration","duration":"2m","condition":{"type":"not","condition":{"type":"comparison","field":"online","operator":"eq","value":true}}}`)
	st := State{}
	run(p, st, ev(0, `{"online":false}`))
	if !run(p, st, ev(2*time.Minute, `{"online":false}`)).Fire {
		t.Fatal("false for 2 minutes")
	}
}

func TestCountWithinWindow(t *testing.T) {
	p := program(t, `{"type":"count_within","window":"10m","min_count":5,"condition":{"type":"comparison","field":"cpu","operator":"gt","value":90}}`)
	st := State{}
	var o Output
	for i := 0; i < 4; i++ {
		o = run(p, st, ev(time.Duration(i)*time.Minute, `{"cpu":95}`))
	}
	if o.Fire {
		t.Fatal("4 of 5")
	}
	run(p, st, ev(4*time.Minute+30*time.Second, `{"cpu":10}`))
	if o = run(p, st, ev(5*time.Minute, `{"cpu":99}`)); !o.Fire {
		t.Fatal("5 violations in 10 minutes")
	}
	// The first violation (minute 0) leaves the window just after minute 10.
	if o = tick(p, st, 10*time.Minute+20*time.Second); o.Fire {
		t.Fatalf("window slid: %+v", o)
	}
}

func TestConsecutive(t *testing.T) {
	p := program(t, `{"type":"consecutive","count":3,"condition":{"type":"comparison","field":"v","operator":"gt","value":1}}`)
	st := State{}
	run(p, st, ev(0, `{"v":2}`))
	run(p, st, ev(1*time.Second, `{"v":2}`))
	run(p, st, ev(2*time.Second, `{"v":0}`))
	run(p, st, ev(3*time.Second, `{"v":2}`))
	run(p, st, ev(4*time.Second, `{"v":2}`))
	if !run(p, st, ev(5*time.Second, `{"v":2}`)).Fire {
		t.Fatal("three in a row after the reset")
	}
}

func TestAggregates(t *testing.T) {
	cases := []struct {
		fn   string
		op   string
		val  float64
		want bool
	}{
		{"avg", "gt", 29.9, true}, {"avg", "gt", 30.1, false},
		{"min", "eq", 10, true}, {"max", "eq", 50, true},
		{"sum", "eq", 120, true}, {"count", "eq", 4, true},
		{"delta", "eq", 40, true}, // last 50 minus first 10
		{"rate", "gt", 0.44, true}, {"rate", "lt", 0.45, true}, // 40 over 90 s
	}
	for _, c := range cases {
		cond, _ := json.Marshal(map[string]any{"type": "aggregate", "function": c.fn, "field": "t", "window": "5m", "operator": c.op, "value": c.val})
		p := program(t, string(cond))
		st := State{}
		var o Output
		for i, v := range []float64{10, 30, 30, 50} {
			b, _ := json.Marshal(map[string]float64{"t": v})
			o = run(p, st, ev(time.Duration(i)*30*time.Second, string(b)))
		}
		if o.Fire != c.want {
			t.Errorf("%s %s %v: got %v (values %v)", c.fn, c.op, c.val, o.Fire, o.Values)
		}
	}
}

func TestMovingAverageSlides(t *testing.T) {
	p := program(t, `{"type":"aggregate","function":"avg","field":"t","window":"1m","operator":"gt","value":30}`)
	st := State{}
	run(p, st, ev(0, `{"t":100}`))
	if !run(p, st, ev(30*time.Second, `{"t":10}`)).Fire {
		t.Fatal("avg 55")
	}
	if run(p, st, ev(70*time.Second, `{"t":10}`)).Fire {
		t.Fatal("100 left the window, avg 10")
	}
}

func TestTemperatureRiseWithinWindow(t *testing.T) {
	p := program(t, `{"type":"aggregate","function":"delta","field":"temperature","window":"2m","operator":"gt","value":15}`)
	st := State{}
	run(p, st, ev(0, `{"temperature":40}`))
	run(p, st, ev(time.Minute, `{"temperature":50}`))
	if !run(p, st, ev(110*time.Second, `{"temperature":56}`)).Fire {
		t.Fatal("rose 16 in under 2 minutes")
	}
	st = State{}
	run(p, st, ev(0, `{"temperature":40}`))
	if run(p, st, ev(3*time.Minute, `{"temperature":60}`)).Fire {
		t.Fatal("over 3 minutes is not within 2")
	}
}

func TestAbsence(t *testing.T) {
	p := program(t, `{"type":"absence","duration":"10m"}`)
	st := State{}
	o := run(p, st, ev(0, `{"x":1}`))
	if o.Fire || !o.Deadline.Equal(t0.Add(10*time.Minute)) {
		t.Fatalf("deadline: %+v", o)
	}
	if tick(p, st, 9*time.Minute).Fire {
		t.Fatal("not silent long enough")
	}
	if !tick(p, st, 10*time.Minute).Fire {
		t.Fatal("silent 10 minutes")
	}
	if run(p, st, ev(11*time.Minute, `{"x":1}`)).Fire {
		t.Fatal("spoke again")
	}
	// Never reported: measured from activation.
	if !tick(p, State{}, 10*time.Minute).Fire {
		t.Fatal("never reported since activation")
	}
}

func TestOutOfOrderEventDoesNotFlipBackwards(t *testing.T) {
	p := program(t, `{"type":"comparison","field":"v","operator":"gt","value":10}`)
	st := State{}
	run(p, st, ev(time.Minute, `{"v":20}`))
	late := ev(30*time.Second, `{"v":1}`)
	o := p.Evaluate(Input{Event: late, Now: t0.Add(time.Minute), OutOfOrder: true, LateTolerance: time.Minute}, st)
	if !o.Fire {
		t.Fatal("an older event must not clear the newer outcome")
	}
}

func TestOutOfOrderEventStillCountsInWindow(t *testing.T) {
	p := program(t, `{"type":"aggregate","function":"count","field":"v","window":"5m","operator":"gte","value":3}`)
	st := State{}
	run(p, st, ev(2*time.Minute, `{"v":1}`))
	run(p, st, ev(3*time.Minute, `{"v":1}`))
	late := ev(time.Minute, `{"v":1}`)
	o := p.Evaluate(Input{Event: late, Now: t0.Add(3 * time.Minute), OutOfOrder: true, LateTolerance: 5 * time.Minute}, st)
	if !o.Fire {
		t.Fatal("late sample inside the window counts")
	}
}

func TestResolveConditionIsEvaluated(t *testing.T) {
	raw := `{"schema_version":1,"name":"h","scope":{"type":"organization"},
		"condition":{"type":"comparison","field":"t","operator":"gt","value":80},
		"resolve":{"type":"comparison","field":"t","operator":"lt","value":70}}`
	r, _ := dsl.ParseAndValidate([]byte(raw), dsl.DefaultLimits(), false)
	p, _ := Compile(r)
	o := run(p, State{}, ev(0, `{"t":75}`))
	if o.Fire || o.Resolve == nil || *o.Resolve {
		t.Fatalf("75 neither fires nor resolves: %+v", o)
	}
}

func TestWindowStateIsBounded(t *testing.T) {
	p := program(t, `{"type":"aggregate","function":"avg","field":"v","window":"1m","operator":"gt","value":0}`)
	st := State{}
	for i := 0; i < 20000; i++ {
		run(p, st, ev(time.Duration(i)*10*time.Millisecond, `{"v":1}`))
	}
	if n := len(st["c"].Buckets); n > BucketsPerWindow*3 {
		t.Fatalf("%d buckets kept", n)
	}
}
