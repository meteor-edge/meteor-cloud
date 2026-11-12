package streams

import "testing"

func TestValidateName(t *testing.T) {
	ok := []string{"energy.power", "a", "temp-1.room_2", "0a.b"}
	bad := []string{"", "Energy.power", "energy..power", "energy.*", ".a", "a.", "-a", "a.b.c.d.e.f.g.h.i", "a b"}
	for _, s := range ok {
		if err := ValidateName(s); err != nil {
			t.Errorf("%q should be valid: %v", s, err)
		}
	}
	for _, s := range bad {
		if err := ValidateName(s); err == nil {
			t.Errorf("%q should be invalid", s)
		}
	}
	if !IsReserved("meteor.lifecycle") || IsReserved("meteorite.x") {
		t.Error("reserved prefix check")
	}
}

func TestValidatePattern(t *testing.T) {
	for _, s := range []string{"energy.*", "energy.**", "*.power", "energy.power"} {
		if err := ValidatePattern(s); err != nil {
			t.Errorf("%q: %v", s, err)
		}
	}
	for _, s := range []string{"**", "energy.**.x", "", "energy.#", "a.+"} {
		if err := ValidatePattern(s); err == nil {
			t.Errorf("%q should be invalid", s)
		}
	}
}

func TestMatch(t *testing.T) {
	cases := []struct {
		p, n string
		want bool
	}{
		{"energy.power", "energy.power", true},
		{"energy.power", "energy.voltage", false},
		{"energy.*", "energy.power", true},
		{"energy.*", "energy.power.l1", false},
		{"energy.*", "energy", false},
		{"energy.**", "energy.power.l1", true},
		{"energy.**", "energy", false},
		{"*.power", "solar.power", true},
		{"*.power", "solar.x.power", false},
	}
	for _, c := range cases {
		if got := Match(c.p, c.n); got != c.want {
			t.Errorf("Match(%q,%q)=%v", c.p, c.n, got)
		}
	}
}

func TestCovers(t *testing.T) {
	cases := []struct {
		grant, sub string
		want       bool
	}{
		{"energy.*", "energy.power", true},
		{"energy.*", "energy.*", true},
		{"energy.*", "energy.**", false},
		{"energy.**", "energy.**", true},
		{"energy.**", "energy.*", true},
		{"energy.**", "energy.power.l1", true},
		{"energy.**", "energy", false},
		{"energy.**", "*.power", false},
		{"energy.power", "energy.*", false},
		{"*.power", "solar.power", true},
		{"*.power", "*.power", true},
		{"energy.*", "other.x", false},
		{"a.*.c", "a.b.c", true},
		{"a.*.c", "a.b.*", false},
	}
	for _, c := range cases {
		if got := Covers(c.grant, c.sub); got != c.want {
			t.Errorf("Covers(%q,%q)=%v want %v", c.grant, c.sub, got, c.want)
		}
	}
}
