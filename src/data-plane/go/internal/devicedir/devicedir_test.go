package devicedir

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestHTTPSyncAndFailClosed(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("X-Data-Plane-Internal-Token") != "tok" || r.URL.Path != "/internal/data-plane/devices" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.Header.Get("If-None-Match") == `"v1"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		json.NewEncoder(w).Encode(Snapshot{Version: "v1", Devices: []Entry{{ID: "D1", OrganizationID: "O1"}}})
	}))
	defer srv.Close()

	now := time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC)
	dir := New(time.Minute)
	dir.SetClock(func() time.Time { return now })
	if _, _, err := dir.Organization("d1"); err != ErrStale {
		t.Fatal("an empty directory must fail closed")
	}
	s := &Syncer{Dir: dir, Source: &HTTPSource{URL: srv.URL, Token: "tok"}, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	if err := s.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if org, ok, err := dir.Organization("d1"); err != nil || !ok || org != "o1" {
		t.Fatalf("%q %v %v", org, ok, err)
	}
	if _, ok, _ := dir.Organization("d2"); ok {
		t.Fatal("unknown device resolved")
	}
	now = now.Add(50 * time.Second)
	if err := s.Once(context.Background()); err != nil || calls != 2 {
		t.Fatalf("304 refresh: %v (calls %d)", err, calls)
	}
	now = now.Add(50 * time.Second) // within maxStale of the 304 refresh
	if err := dir.Ready(); err != nil {
		t.Fatal("a confirmed snapshot must stay current")
	}
	now = now.Add(2 * time.Minute)
	if _, _, err := dir.Organization("d1"); err != ErrStale {
		t.Fatal("a stale directory must fail closed")
	}
	bad := &Syncer{Dir: dir, Source: &HTTPSource{URL: srv.URL, Token: "wrong"}, Log: s.Log}
	if err := bad.Once(context.Background()); err == nil {
		t.Fatal("a refused token must be an error")
	}
}
