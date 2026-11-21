package update

import "testing"

func TestNewer(t *testing.T) {
	cases := []struct {
		candidate, current string
		want               bool
	}{
		{"0.2.0", "0.1.9", true},
		{"0.1.10", "0.1.9", true},
		{"1.0.0", "1.0.0-rc.1", true},
		{"1.0.0-rc.1", "1.0.0", false},
		{"0.0.0-edge.13+abc", "0.0.0-edge.12+def", true},
		{"0.0.0-edge.9", "0.0.0-edge.10", false},
		{"0.0.0-edge.1", "0.0.0-dev+abc", true}, // "dev" < "edge"
		{"1.0.0-alpha.1", "1.0.0-alpha", true},
		{"1.0.0-alpha.beta", "1.0.0-alpha.1", true}, // words sort after numbers
		{"0.1.0", "0.1.0+other", false},
		{"v0.2.0", "meteorcli 0.1.0", true},
		{"0.2.0", "systemd-test", true}, // an unparseable current build is older
		{"junk", "0.1.0", false},
		{"1.2", "0.1.0", false},
		{"1.2.3-", "0.1.0", false},
	}
	for _, c := range cases {
		if got := Newer(c.candidate, c.current); got != c.want {
			t.Errorf("Newer(%q, %q) = %v, want %v", c.candidate, c.current, got, c.want)
		}
	}
}

func TestSame(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"0.1.0", "0.1.0", true},
		{"0.1.0+a", "0.1.0+b", true},
		{"0.1.0-rc.1", "0.1.0", false},
		{"systemd-test", "systemd-test", true},
		{"systemd-test", "0.1.0", false},
	}
	for _, c := range cases {
		if got := Same(c.a, c.b); got != c.want {
			t.Errorf("Same(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}
