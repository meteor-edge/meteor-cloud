package agent

import (
	"context"
	"errors"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/doctor"
	"meteorcloud/meteorcli/internal/identity"
)

type fakePinger struct {
	err   error
	calls []string
}

func (f *fakePinger) Call(ctx context.Context, op string, _, _ any) error {
	if _, ok := ctx.Deadline(); !ok {
		return errors.New("no timeout on the helper call")
	}
	f.calls = append(f.calls, op)
	return f.err
}

func check(r doctor.Report, prefix string) (doctor.Check, bool) {
	for _, c := range r.Checks {
		if strings.HasPrefix(c.Name, prefix) {
			return c, true
		}
	}
	return doctor.Check{}, false
}

func TestDiagnose(t *testing.T) {
	p := agentRoot(t)
	d := testDeps(nil)
	d.Paths, d.Version = p, "9.9.9"
	d.Identity = identity.NewSource(p)
	if err := d.Identity.Load(); err != nil {
		t.Fatal(err)
	}
	d.Health.Set("deploy", StateParked, errors.New("artifacts directory not writable"))
	d.Health.Set("heartbeat", StateRunning, nil)

	tests := []struct {
		name       string
		helper     HelperPinger
		helperWant doctor.Level
		helperSeen bool
	}{
		{"helper answers", &fakePinger{}, doctor.OK, true},
		{"helper down", &fakePinger{err: errors.New("connection refused")}, doctor.Fail, true},
		{"helper not checked", nil, doctor.Warn, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := Diagnose(context.Background(), d, tt.helper)
			if r.Version != "9.9.9" || r.OK() {
				t.Errorf("version %q, ok %v: a parked module must fail the report", r.Version, r.OK())
			}
			if c, ok := check(r, "module deploy"); !ok || c.Level != doctor.Fail || !strings.Contains(c.Hint, "meteorcli module restart deploy") {
				t.Errorf("deploy check = %+v (%v)", c, ok)
			}
			c, ok := check(r, "root helper")
			if ok != tt.helperSeen || (ok && c.Level != tt.helperWant) {
				t.Errorf("helper check = %+v (%v)", c, ok)
			}
			if f, isFake := tt.helper.(*fakePinger); isFake && (len(f.calls) != 1 || f.calls[0] != "ping") {
				t.Errorf("helper calls = %v", f.calls)
			}
			if _, ok := check(r, "disk"); !ok {
				t.Error("no disk check")
			}
		})
	}
}

func TestFreeBytes(t *testing.T) {
	if n, err := freeBytes(t.TempDir()); err != nil || n == 0 {
		t.Errorf("freeBytes = %d, %v", n, err)
	}
	if _, err := freeBytes("/does/not/exist"); err == nil {
		t.Error("a missing directory must be an error")
	}
}
