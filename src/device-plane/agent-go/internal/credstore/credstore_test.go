package credstore

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/fakecp"
)

func issued(application, environment string) fakecp.Reply {
	return fakecp.Created(map[string]any{
		"token": "tok-" + application, "credential_id": "c1", "expires_at": "2999-01-01T00:00:00Z",
		"refresh_after": "2998-01-01T00:00:00Z", "endpoint": "https://data.example", "application": application,
		"organization_id": "o1", "device_id": "d-1", "environment": environment,
	})
}

func client(t *testing.T, cp *fakecp.Server) *api.Client {
	c, err := api.New(api.Options{BaseURL: cp.URL, Version: "1.0.0", HTTPClient: cp.Client()})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestTokenIsRewrittenInPlace(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	if err := writeInPlace(path, []byte("a-long-first-token\n"), true); err != nil {
		t.Fatal(err)
	}
	before, _ := os.Stat(path)
	if err := writeInPlace(path, []byte("short\n"), false); err != nil {
		t.Fatal(err)
	}
	after, _ := os.Stat(path)
	data, _ := os.ReadFile(path)
	if string(data) != "short\n" || after.Mode().Perm() != 0o644 {
		t.Fatalf("content %q mode %v", data, after.Mode())
	}
	if before.Sys().(*syscall.Stat_t).Ino != after.Sys().(*syscall.Stat_t).Ino {
		t.Fatal("the token file was replaced; a bind mount would keep the old one")
	}
}

func TestStore(t *testing.T) {
	now := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	s := Store{Root: t.TempDir(), Now: func() time.Time { return now }}
	b := Binding{Application: "app-1"}
	key := "shop/main"
	if s.Current(key, b) != nil {
		t.Fatal("credential before any was stored")
	}
	dir := s.Dir(key)
	if filepath.Base(dir)[:10] != "shop_main-" || s.Dir("shop_main") == dir {
		t.Fatalf("dir %s", dir)
	}
	_ = os.MkdirAll(dir, 0o700)
	_ = os.WriteFile(filepath.Join(dir, "token"), []byte("tok\n"), 0o644)
	meta := Meta{WorkloadKey: key, Application: "app-1", Environment: "production", RefreshAfter: "2026-05-01T00:00:00Z",
		ExpiresAt: "2026-07-01T00:00:00+00:00", Env: map[string]string{"METEORCLOUD_APP_ID": "app-1"}}
	data, _ := json.Marshal(meta)
	_ = os.WriteFile(filepath.Join(dir, "meta.json"), data, 0o600)

	if c := s.Current(key, b); c == nil || c.Env["METEORCLOUD_APP_ID"] != "app-1" {
		t.Fatalf("current = %+v", c)
	}
	if s.Current(key, Binding{Application: "app-2"}) != nil {
		t.Error("another application's token reused")
	}
	if s.Current(key, Binding{Application: "app-1", Environment: "staging"}) != nil {
		t.Error("another environment's token reused")
	}
	if !s.RefreshDue(key) || s.Expired(key) {
		t.Errorf("refresh due %v, expired %v", s.RefreshDue(key), s.Expired(key))
	}
	if all := s.All(); len(all) != 1 || all[0].WorkloadKey != key {
		t.Errorf("All = %+v", all)
	}
	s.Prune(map[string]bool{"other": true})
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Error("prune kept a workload that is no longer bound")
	}
}

func TestMint(t *testing.T) {
	cp := fakecp.New(t)
	c := client(t, cp)
	s := Store{Root: t.TempDir()}
	ctx := context.Background()

	cp.On("POST", "/api/v1/agent/applications/credentials", issued("app-1", "production"))
	if _, err := s.Mint(ctx, c, "dev", "web", Binding{Application: "app-1"}, true); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("a rotation created a credential: %v", err)
	}
	cred, err := s.Mint(ctx, c, "dev", "web", Binding{Application: "app-1"}, false)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(cred.TokenFile)
	if string(data) != "tok-app-1\n" || cred.Env["METEORCLOUD_ENDPOINT"] != "https://data.example" ||
		cred.Env["METEORCLOUD_TOKEN_FILE"] != TokenMountPath {
		t.Fatalf("token %q env %v", data, cred.Env)
	}
	if m, ok := s.Meta("web"); !ok || m.WorkloadKey != "web" || m.Version != metaVersion {
		t.Fatalf("meta %+v", m)
	}
	if got := cp.Calls("POST", "/api/v1/agent/applications/credentials"); got[len(got)-1].Body["workload_key"] != "web" {
		t.Errorf("request %v", got[len(got)-1].Body)
	}

	// Another binding is refused and the stored token stays.
	cp.On("POST", "/api/v1/agent/applications/credentials", issued("app-2", "production"))
	_, err = s.Mint(ctx, c, "dev", "web", Binding{Application: "app-1"}, true)
	var ce Error
	if !errors.As(err, &ce) || !strings.Contains(ce.Msg, "app-2") {
		t.Fatalf("err %v", err)
	}
	if data, _ := os.ReadFile(cred.TokenFile); string(data) != "tok-app-1\n" {
		t.Error("the stored token was replaced by another application's")
	}

	cp.On("POST", "/api/v1/agent/applications/credentials", fakecp.Fail(401, "invalid_token"))
	if _, err := s.Mint(ctx, c, "dev", "web", Binding{Application: "app-1"}, true); !api.IsAuth(err) {
		t.Errorf("a refused device token is %v", err)
	}
	cp.On("POST", "/api/v1/agent/applications/credentials", fakecp.Fail(503, "down"))
	if _, err := s.Mint(ctx, c, "dev", "web", Binding{Application: "app-1"}, true); !errors.As(err, &ce) {
		t.Errorf("an outage is %v", err)
	}
}

func TestRotationEdgeCases(t *testing.T) {
	cp := fakecp.New(t)
	c := client(t, cp)
	s := Store{Root: t.TempDir()}
	ctx := context.Background()
	b := Binding{Application: "app-1"}
	cp.On("POST", "/api/v1/agent/applications/credentials", issued("app-1", "production"))
	cred, err := s.Mint(ctx, c, "dev", "web", b, false)
	if err != nil {
		t.Fatal(err)
	}
	// Unknown meta.json keys survive a rotation.
	metaPath := filepath.Join(s.Dir("web"), "meta.json")
	data, _ := os.ReadFile(metaPath)
	_ = os.WriteFile(metaPath, []byte(strings.Replace(string(data), "{", `{"future_field": 7,`, 1)), 0o600)
	if _, err := s.Mint(ctx, c, "dev", "web", b, true); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(metaPath); !strings.Contains(string(data), `"future_field": 7`) {
		t.Errorf("unknown key dropped:\n%s", data)
	}
	// Deploy stored another binding meanwhile: the rotation does not overwrite it.
	_ = os.WriteFile(metaPath, []byte(`{"version":1,"workload_key":"web","application":"app-2","environment":"production"}`), 0o600)
	var ce Error
	if _, err := s.Mint(ctx, c, "dev", "web", b, true); !errors.As(err, &ce) {
		t.Errorf("stale binding rotated: %v", err)
	}
	// The token file was deleted: a rotation does not create a new inode.
	_ = os.WriteFile(metaPath, data, 0o600)
	_ = os.Remove(cred.TokenFile)
	if _, err := s.Mint(ctx, c, "dev", "web", b, true); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("missing token file: %v", err)
	}
	if _, err := os.Stat(cred.TokenFile); err == nil {
		t.Error("a rotation recreated the token file")
	}
	// A damaged meta.json is moved aside, not left to be skipped forever.
	_ = os.WriteFile(metaPath, []byte("{broken"), 0o600)
	if _, ok := s.Meta("web"); ok {
		t.Error("damaged meta read as valid")
	}
	if matches, _ := filepath.Glob(metaPath + ".corrupt-*"); len(matches) != 1 {
		t.Errorf("not quarantined: %v", matches)
	}
}
