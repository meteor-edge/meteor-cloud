package identity

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

const (
	orgA = "00000000-0000-0000-0000-00000000000a"
	orgB = "00000000-0000-0000-0000-00000000000b"
)

func snapshot(now time.Time) *Snapshot {
	dev := "00000000-0000-0000-0000-0000000000d1"
	return &Snapshot{
		Version: "v1",
		Applications: []SnapshotApplication{
			{ID: "app-a", OrganizationID: orgA, Slug: "collector", Permissions: Permissions{
				Publish: []string{"energy.*"}, Subscribe: []string{"temperature.**"}, Read: []string{"energy.power"},
				SendCommands: []string{"detector"},
			}},
			{ID: "app-b", OrganizationID: orgB, Slug: "collector"},
		},
		Credentials: []SnapshotCredential{
			{ID: "c1", OrganizationID: orgA, ApplicationID: "app-a", DeviceID: &dev, Environment: "production",
				TokenHash: HashToken("mca_alpha"), ExpiresAt: now.Add(time.Hour)},
			{ID: "c2", OrganizationID: orgB, ApplicationID: "app-b", Environment: "production",
				TokenHash: HashToken("mca_beta"), ExpiresAt: now.Add(time.Hour)},
			{ID: "c3", OrganizationID: orgA, ApplicationID: "app-a", Environment: "production",
				TokenHash: HashToken("mca_expired"), ExpiresAt: now.Add(-time.Second)},
			// Credential pointing at another organization's application is ignored.
			{ID: "c4", OrganizationID: orgA, ApplicationID: "app-b", Environment: "production",
				TokenHash: HashToken("mca_cross"), ExpiresAt: now.Add(time.Hour)},
		},
	}
}

func TestAuthenticate(t *testing.T) {
	now := time.Now()
	s := NewStore(time.Hour)
	if _, err := s.Authenticate("mca_alpha"); err != ErrPolicyStale {
		t.Fatalf("empty store should be stale, got %v", err)
	}
	s.Apply(snapshot(now))

	id, err := s.Authenticate("mca_alpha")
	if err != nil {
		t.Fatal(err)
	}
	if id.OrganizationID != orgA || id.Application != "collector" || id.DeviceID == "" {
		t.Fatalf("unexpected identity %+v", id)
	}
	for _, bad := range []string{"mca_expired", "mca_cross", "mca_unknown", "dev_alpha", "", "alpha"} {
		if _, err := s.Authenticate(bad); err != ErrUnauthenticated {
			t.Errorf("%q: want ErrUnauthenticated, got %v", bad, err)
		}
	}
	other, _ := s.Authenticate("mca_beta")
	if other.OrganizationID != orgB || other.DeviceID != "" {
		t.Fatalf("org B identity wrong: %+v", other)
	}
}

func TestPermissions(t *testing.T) {
	s := NewStore(0)
	s.Apply(snapshot(time.Now()))
	id, _ := s.Authenticate("mca_alpha")
	if !id.CanPublish("energy.power") || id.CanPublish("energy.power.l1") || id.CanPublish("water.flow") {
		t.Error("publish grants")
	}
	if !id.CanSubscribe("temperature.room") || !id.CanSubscribe("temperature.**") || id.CanSubscribe("energy.power") {
		t.Error("subscribe grants")
	}
	if !id.CanRead("energy.power") || id.CanRead("energy.voltage") {
		t.Error("read grants")
	}
	if !id.CanSendCommandTo("detector") || id.CanSendCommandTo("collector") {
		t.Error("command grants")
	}
	b, _ := s.Authenticate("mca_beta")
	if b.CanPublish("energy.power") || b.CanSubscribe("temperature.room") || b.CanRead("energy.power") {
		t.Error("an application without grants must have no access")
	}
}

func TestRevocationAndStaleness(t *testing.T) {
	now := time.Now()
	clock := now
	s := NewStore(10 * time.Minute)
	s.SetClock(func() time.Time { return clock })
	s.Apply(snapshot(now))
	id, _ := s.Authenticate("mca_alpha")

	// Control plane down for a while: still serving.
	clock = now.Add(9 * time.Minute)
	if _, err := s.Authenticate("mca_alpha"); err != nil {
		t.Fatalf("should serve from the last snapshot: %v", err)
	}
	// Too old: fail closed.
	clock = now.Add(11 * time.Minute)
	if _, err := s.Authenticate("mca_alpha"); err != ErrPolicyStale {
		t.Fatalf("want ErrPolicyStale, got %v", err)
	}
	if s.Ready() {
		t.Fatal("stale store must not be ready")
	}
	// A 304 from the control plane refreshes it.
	s.Touch()
	if !s.StillValid(id) {
		t.Fatal("credential should be valid again after Touch")
	}
	// Revocation: the credential disappears from the next snapshot.
	next := snapshot(clock)
	next.Version = "v2"
	next.Credentials = next.Credentials[1:]
	s.Apply(next)
	if _, err := s.Authenticate("mca_alpha"); err != ErrUnauthenticated {
		t.Fatalf("revoked credential accepted: %v", err)
	}
	if s.StillValid(id) {
		t.Fatal("revoked credential still valid for an open connection")
	}
}

func TestHTTPSourceAndSyncer(t *testing.T) {
	snap := snapshot(time.Now())
	fail := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Data-Plane-Internal-Token") != "secret" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if fail {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		if r.Header.Get("If-None-Match") == `"`+snap.Version+`"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		json.NewEncoder(w).Encode(snap)
	}))
	defer srv.Close()

	store := NewStore(0)
	syncer := &Syncer{Store: store, Source: &HTTPSource{URL: srv.URL, Token: "secret"},
		Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	if err := syncer.SyncOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Authenticate("mca_alpha"); err != nil {
		t.Fatal(err)
	}
	if err := syncer.SyncOnce(context.Background()); err != nil { // 304 path
		t.Fatal(err)
	}
	fail = true
	if err := syncer.SyncOnce(context.Background()); err == nil {
		t.Fatal("expected an error while the control plane is failing")
	}
	if _, err := store.Authenticate("mca_alpha"); err != nil {
		t.Fatalf("a failed sync must keep the previous snapshot: %v", err)
	}
	bad := &Syncer{Store: NewStore(0), Source: &HTTPSource{URL: srv.URL, Token: "wrong"}, Log: syncer.Log}
	if err := bad.SyncOnce(context.Background()); err == nil {
		t.Fatal("wrong internal token must fail")
	}
}
