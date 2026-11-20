package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseMeteorenv(t *testing.T) {
	tests := []struct {
		name, in string
		want     map[string]string
	}{
		{"plain", "A=1\nB=two\n", map[string]string{"A": "1", "B": "two"}},
		{"bom, comments, CRLF", "\ufeff# x\r\n\r\nA=1\r\n", map[string]string{"A": "1"}},
		{"quotes", `A="x y"` + "\nB='z'\nC=\"open\n", map[string]string{"A": "x y", "B": "z", "C": `"open`}},
		{"junk lines", "not a pair\n1A=x\nexport B=y\n=z\n", map[string]string{}},
		{"later wins", "A=1\nA=2\n", map[string]string{"A": "2"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ParseMeteorenv(tt.in)
			if len(got) != len(tt.want) {
				t.Fatalf("got %v, want %v", got, tt.want)
			}
			for k, v := range tt.want {
				if got[k] != v {
					t.Errorf("%s = %q, want %q", k, got[k], v)
				}
			}
		})
	}
}

func TestFindAndReadMeteorenv(t *testing.T) {
	root := t.TempDir()
	put := func(p, s string) {
		full := filepath.Join(root, p)
		_ = os.MkdirAll(filepath.Dir(full), 0o755)
		if err := os.WriteFile(full, []byte(s), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if got := FindMeteorenv(root, ""); len(got) != 0 {
		t.Fatalf("found %v in an empty root", got)
	}
	put("boot/meteorenv.txt", "METEOR_DOMAIN=old.example\n")
	put("boot/firmware/.meteorenv", "METEOR_DOMAIN= meteor.example \nMETEOR_API_KEY=key_1\nMETEOR_NAME=cam\n")
	put("media/sd/meteorenv", "METEOR_API_BASE=https://x.example\n")
	got := FindMeteorenv(root, "/media/sd")
	want := []string{"media/sd/meteorenv", "boot/firmware/.meteorenv", "boot/meteorenv.txt"}
	if len(got) != len(want) {
		t.Fatalf("found %v", got)
	}
	for i := range want {
		if got[i] != filepath.Join(root, want[i]) {
			t.Errorf("[%d] = %s, want %s", i, got[i], want[i])
		}
	}
	m, err := ReadMeteorenv(got[1])
	if err != nil || m != (Meteorenv{Domain: "meteor.example", APIKey: "key_1", Name: "cam"}) || m.Empty() {
		t.Errorf("read %+v %v", m, err)
	}
	if !(Meteorenv{Name: "only a name"}).Empty() {
		t.Error("a name alone is not enough to enroll")
	}
	if _, err := ReadMeteorenv(filepath.Join(root, "nope")); err == nil {
		t.Error("missing file read")
	}
}
