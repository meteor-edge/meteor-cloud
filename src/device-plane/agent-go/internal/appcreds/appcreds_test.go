package appcreds

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/credstore"
	"meteorcloud/meteorcli/internal/fakecp"
)

func reply(token string) fakecp.Reply {
	return fakecp.Created(map[string]any{
		"token": token, "credential_id": "c1", "expires_at": "2999-01-01T00:00:00Z",
		"refresh_after": "2998-01-01T00:00:00Z", "endpoint": nil, "application": "app-1",
		"organization_id": "o1", "device_id": agenttest.DeviceID, "environment": "production",
	})
}

// due marks a stored credential as due for rotation.
func due(t *testing.T, s credstore.Store, key string) {
	t.Helper()
	path := filepath.Join(s.Dir(key), "meta.json")
	var m map[string]any
	data, _ := os.ReadFile(path)
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	m["refresh_after"] = "2000-01-01T00:00:00Z"
	data, _ = json.Marshal(m)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestRotatesDueTokensInPlace(t *testing.T) {
	cp := fakecp.New(t)
	d := agenttest.Deps(t, cp, nil, nil)
	s := credstore.Store{Root: d.Paths.AppCredentialsDir()}
	ctx := context.Background()
	cp.On("POST", "/api/v1/agent/applications/credentials", reply("first"), reply("second"))
	cred, err := s.Mint(ctx, d.API, agenttest.Token, "web", credstore.Binding{Application: "app-1"}, false)
	if err != nil {
		t.Fatal(err)
	}
	Rotate(ctx, d, s)
	if n := len(cp.Calls("POST", "/api/v1/agent/applications/credentials")); n != 1 {
		t.Fatalf("a fresh token was rotated (%d calls)", n)
	}
	due(t, s, "web")
	Rotate(ctx, d, s)
	if data, _ := os.ReadFile(cred.TokenFile); string(data) != "second\n" {
		t.Fatalf("token %q", data)
	}
	if s.RefreshDue("web") {
		t.Error("still due after the rotation")
	}
}

func TestRefusedTokenGoesToIdentity(t *testing.T) {
	cp := fakecp.New(t)
	d := agenttest.Deps(t, cp, nil, nil)
	s := credstore.Store{Root: d.Paths.AppCredentialsDir()}
	cp.On("POST", "/api/v1/agent/applications/credentials", reply("first"), fakecp.Fail(401, "invalid_token"))
	if _, err := s.Mint(context.Background(), d.API, agenttest.Token, "web", credstore.Binding{Application: "app-1"}, false); err != nil {
		t.Fatal(err)
	}
	due(t, s, "web")
	Rotate(context.Background(), d, s)
	if _, err := d.Identity.Token(); err == nil {
		t.Fatal("the refused device token is still in use")
	}
}

func TestModuleRunsOnATimer(t *testing.T) {
	cp := fakecp.New(t)
	d := agenttest.Deps(t, cp, nil, nil)
	s := credstore.Store{Root: d.Paths.AppCredentialsDir()}
	cp.On("POST", "/api/v1/agent/applications/credentials", reply("first"), reply("second"))
	if _, err := s.Mint(context.Background(), d.API, agenttest.Token, "web", credstore.Binding{Application: "app-1"}, false); err != nil {
		t.Fatal(err)
	}
	due(t, s, "web")
	agenttest.Start(t, &Module{Every: 20 * time.Millisecond}, d)
	cp.Wait(t, "POST", "/api/v1/agent/applications/credentials", 2, 5*time.Second)
}
