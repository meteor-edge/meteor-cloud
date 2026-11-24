package state

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type pins struct {
	Pinned map[string]string `json:"pinned"`
}

func TestStoreRoundTripKeepsUnknownKeys(t *testing.T) {
	s, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(s.Dir(), "signing-keys.json"), []byte(`{"pinned":{"a":"1"},"later":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var p pins
	v, err := s.Read("signing-keys.json", &p)
	if err != nil || v != 0 || p.Pinned["a"] != "1" {
		t.Fatalf("Read = %d, %v, %+v", v, err, p)
	}
	p.Pinned["b"] = "2"
	if err := s.Write("signing-keys.json", 2, p); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(s.Dir(), "signing-keys.json"))
	if !strings.Contains(string(b), `"later": true`) || !strings.Contains(string(b), `"version": 2`) {
		t.Errorf("file = %s", b)
	}
	var again pins
	if v, err := s.Read("signing-keys.json", &again); err != nil || v != 2 || again.Pinned["b"] != "2" {
		t.Errorf("Read again = %d, %v, %+v", v, err, again)
	}
}

func TestStoreErrors(t *testing.T) {
	s, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	var p pins
	if _, err := s.Read("missing.json", &p); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("missing: %v", err)
	}
	for _, bad := range []string{"../x", ".hidden", "A.json", ""} {
		if _, err := s.Read(bad, &p); err == nil {
			t.Errorf("Read(%q): want error", bad)
		}
		if err := s.Write(bad, 1, p); err == nil {
			t.Errorf("Write(%q): want error", bad)
		}
	}
	tests := map[string]string{
		"broken.json":  `{"pinned":`,
		"array.json":   `[1,2]`,
		"version.json": `{"version":"x"}`,
		"type.json":    `{"pinned":"not a map"}`,
	}
	for name, body := range tests {
		if err := os.WriteFile(filepath.Join(s.Dir(), name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := s.Read(name, &p); !errors.Is(err, ErrCorrupt) {
			t.Errorf("%s: err = %v, want ErrCorrupt", name, err)
		}
		if m, _ := filepath.Glob(filepath.Join(s.Dir(), name+".corrupt-*")); len(m) != 1 {
			t.Errorf("%s: not quarantined", name)
		}
	}
	if err := s.Remove("missing.json"); err != nil {
		t.Error(err)
	}
}

func TestOutboxPutPendingAck(t *testing.T) {
	o, err := NewOutbox(t.TempDir(), 0)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := o.Put("software", "app-1", map[string]string{"sync_status": "updating"})
	_, _ = o.Put("action", "", map[string]string{"status": "done"})
	b, _ := o.Put("software", "app-1", map[string]string{"sync_status": "in_sync"})
	_, _ = o.Put("software", "app-2", map[string]string{"sync_status": "failed"})

	sw, err := o.Pending("software")
	if err != nil {
		t.Fatal(err)
	}
	if len(sw) != 2 || sw[0].ID != b.ID || !strings.Contains(string(sw[0].Body), "in_sync") {
		t.Fatalf("software pending = %+v", sw)
	}
	if all, _ := o.Pending(""); len(all) != 3 {
		t.Errorf("all pending = %d", len(all))
	}

	// A reopened outbox (after a reboot) still has them, oldest first.
	o2, _ := NewOutbox(o.dir, 0)
	all, _ := o2.Pending("")
	if all[0].Kind != "action" {
		t.Errorf("order %+v", all)
	}
	if err := o2.Ack(a.ID, b.ID, all[0].ID); err != nil {
		t.Fatal(err)
	}
	if left, _ := o2.Pending(""); len(left) != 1 || left[0].Key != "app-2" {
		t.Errorf("left %+v", left)
	}
	if err := o2.Ack("../etc"); err == nil {
		t.Error("Ack with a path: want error")
	}
}

func TestOutboxLimitDropsOldest(t *testing.T) {
	o, _ := NewOutbox(t.TempDir(), 3)
	for i := range 5 {
		if _, err := o.Put("action", "", i); err != nil {
			t.Fatal(err)
		}
	}
	all, _ := o.Pending("")
	if len(all) != 3 || string(all[0].Body) != "2" || o.Dropped() != 2 {
		t.Errorf("pending %d first %s dropped %d", len(all), all[0].Body, o.Dropped())
	}
}

func TestOutboxQuarantinesDamagedReport(t *testing.T) {
	o, _ := NewOutbox(t.TempDir(), 0)
	o.now = func() time.Time { return time.Unix(1700000000, 0) }
	_ = os.WriteFile(filepath.Join(o.dir, "00000000000000000001-000001.json"), []byte("{"), 0o600)
	_, _ = o.Put("action", "", "ok")
	all, err := o.Pending("")
	if err != nil || len(all) != 1 {
		t.Fatalf("pending %+v, %v", all, err)
	}
	if _, err := os.Stat(filepath.Join(o.dir, "00000000000000000001-000001.json.corrupt-1700000000")); err != nil {
		t.Error("damaged report not quarantined")
	}
}

func TestOutboxConcurrentPut(t *testing.T) {
	o, _ := NewOutbox(t.TempDir(), 0)
	var wg sync.WaitGroup
	for i := range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := o.Put("action", "", i); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if all, _ := o.Pending(""); len(all) != 20 {
		t.Errorf("pending %d, want 20", len(all))
	}
}

func TestOutboxOrderIgnoresClockJumps(t *testing.T) {
	o, _ := NewOutbox(t.TempDir(), 1)
	clock := time.Unix(2000, 0)
	o.now = func() time.Time { return clock }
	if _, err := o.Put("action", "", "A"); err != nil {
		t.Fatal(err)
	}
	clock = clock.Add(-time.Hour) // the wall clock moves back
	b, err := o.Put("action", "", "B")
	if err != nil {
		t.Fatal(err)
	}
	all, _ := o.Pending("")
	if len(all) != 1 || all[0].ID != b.ID {
		t.Fatalf("pending %+v, want only B", all)
	}

	// A reopened outbox continues after the highest id on disk.
	o2, _ := NewOutbox(o.dir, 0)
	c, _ := o2.Put("action", "", "C")
	if c.ID <= b.ID {
		t.Errorf("id went down after reopening: %s <= %s", c.ID, b.ID)
	}
}

func TestStoreWriteVersionWinsAndUnreadableFileIsKept(t *testing.T) {
	s, _ := NewStore(t.TempDir())
	type withVersion struct {
		Version int    `json:"version"`
		Name    string `json:"name"`
	}
	if err := s.Write("x.json", 3, withVersion{Version: 9, Name: "n"}); err != nil {
		t.Fatal(err)
	}
	var got withVersion
	if v, err := s.Read("x.json", &got); err != nil || v != 3 || got.Name != "n" {
		t.Errorf("Read = %d, %v, %+v", v, err, got)
	}

	if os.Geteuid() == 0 {
		t.Skip("root reads any file")
	}
	path := filepath.Join(s.Dir(), "x.json")
	if err := os.Chmod(path, 0o000); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(path, 0o600)
	if err := s.Write("x.json", 4, withVersion{Name: "new"}); err == nil {
		t.Error("Write replaced a file it could not read")
	}
}
