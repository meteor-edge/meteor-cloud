package deploy

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
)

// issue scripts POST /applications/credentials to answer for application.
func (e *env) issue(application string) {
	e.cp.On("POST", "/api/v1/agent/applications/credentials", fakecp.Created(map[string]any{
		"token": "tok-" + application, "credential_id": "c-" + application, "expires_at": "2999-01-01T00:00:00Z",
		"refresh_after": "2998-01-01T00:00:00Z", "endpoint": "https://data.example", "application": application,
		"organization_id": "o1", "device_id": agenttest.DeviceID, "environment": "production",
	}))
}

func (e *env) mints() int { return len(e.cp.Calls("POST", "/api/v1/agent/applications/credentials")) }

func boundItem(release, version string, revision int, image, application string, gate bool) map[string]any {
	spec := map[string]any{"image": image, "meteorcloud": map[string]any{"application": application}}
	if gate {
		spec["health_check"] = map[string]any{"timeout_seconds": 4, "stable_seconds": 0}
	}
	return dockerItem(release, version, revision, spec)
}

func (e *env) credDirs() []string {
	entries, _ := os.ReadDir(e.d.Paths.AppCredentialsDir())
	var out []string
	for _, en := range entries {
		if en.IsDir() {
			out = append(out, en.Name())
		}
	}
	return out
}

func TestRefusedItemKeepsItsCredentials(t *testing.T) {
	e := newEnv(t)
	e.issue("app-1")
	e.desire(boundItem(rel1, "1", 1, "img1", "app-1", false))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync {
		t.Fatalf("report %v", got)
	}
	tampered := signed(t, boundItem(rel2, "2", 2, "img2", "app-1", false))
	tampered["spec"].(map[string]any)["image"] = "evil"
	e.cp.On("GET", "/api/v1/agent/software", fakecp.OK([]any{tampered}))
	if got := e.cycle()["demo-app"]; !strings.Contains(got.lastError(), "does not verify") {
		t.Fatalf("report %v", got)
	}
	if len(e.credDirs()) != 1 {
		t.Fatalf("a refused item dropped the stored credentials: %v", e.credDirs())
	}
}

func TestRefusedSigningKeysStopTheCycle(t *testing.T) {
	e := newEnv(t)
	stale := filepath.Join(e.d.Paths.AppCredentialsDir(), "old-0123456789ab")
	if err := os.MkdirAll(stale, 0o700); err != nil {
		t.Fatal(err)
	}
	e.cp.On("GET", "/api/v1/agent/signing-keys", fakecp.Fail(401, "invalid_token"))
	e.desire(dockerItem(rel1, "1", 1, map[string]any{"image": "nginx"}))
	if got := e.cycle(); len(got) != 0 {
		t.Fatalf("reports %v", got)
	}
	if _, err := e.d.Identity.Token(); err == nil {
		t.Error("the refused token is still in use")
	}
	if _, err := os.Stat(stale); err != nil {
		t.Error("credentials were pruned in a cycle whose token was refused")
	}
}

func TestRollbackNeverUsesAnotherApplicationsToken(t *testing.T) {
	e := newEnv(t)
	e.issue("app-1")
	e.desire(boundItem(rel1, "1", 1, "img1", "app-1", false))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync {
		t.Fatalf("v1 %v", got)
	}
	// v2 is bound to app-2 and breaks; the server only issues app-2 tokens now.
	e.issue("app-2")
	e.host.broken["img2"] = true
	e.desire(boundItem(rel2, "2", 2, "img2", "app-2", true))
	got := e.cycle()["demo-app"]
	if got.status() != statusFailed || !strings.Contains(got.lastError(), "rolling back to 1 also failed") ||
		!strings.Contains(got.lastError(), "issued credentials for application app-2") {
		t.Fatalf("report %v", got)
	}
	var run helper.DockerRunRequest
	e.host.last(t, "docker.run", &run)
	if run.Image != "img2" {
		t.Errorf("v1 was started with app-2's token (last run %s)", run.Image)
	}
}

func TestRolledBackReleaseKeepsItsToken(t *testing.T) {
	e := newEnv(t)
	e.issue("app-1")
	e.desire(boundItem(rel1, "1", 1, "img1", "app-1", false))
	e.cycle()
	e.host.broken["img2"] = true
	e.desire(boundItem(rel2, "2", 2, "img2", "app-1", true))
	if got := e.cycle()["demo-app"]; got.status() != statusRolledBack {
		t.Fatalf("v2 %v", got)
	}
	// The kept release's token falls due: the appcreds module rotates it, not
	// deploy, and v2 stays refused without a new apply.
	dir := filepath.Join(e.d.Paths.AppCredentialsDir(), e.credDirs()[0])
	var meta map[string]any
	data, _ := os.ReadFile(filepath.Join(dir, "meta.json"))
	_ = json.Unmarshal(data, &meta)
	meta["refresh_after"] = "2000-01-01T00:00:00Z"
	data, _ = json.Marshal(meta)
	_ = os.WriteFile(filepath.Join(dir, "meta.json"), data, 0o600)
	mints, runs := e.mints(), e.host.count("docker.run")
	if got := e.cycle()["demo-app"]; got.status() != statusRolledBack || e.mints() != mints || e.host.count("docker.run") != runs {
		t.Fatalf("report %v, mints %d->%d, runs %d->%d", got, mints, e.mints(), runs, e.host.count("docker.run"))
	}
	// Its token file is lost: v1 is applied again, not v2.
	_ = os.RemoveAll(e.d.Paths.AppCredentialsDir())
	got := e.cycle()["demo-app"]
	var run helper.DockerRunRequest
	e.host.last(t, "docker.run", &run)
	if got.status() != statusRolledBack || run.Image != "img1" || e.host.count("docker.run") != runs+1 {
		t.Fatalf("report %v, last run %s", got, run.Image)
	}
}

func TestRefusedProgressStopsTheDownload(t *testing.T) {
	e := newEnv(t)
	it := dockerItem(rel1, "1", 1, map[string]any{"container_name": "demo-app"})
	it["artifact_id"], it["artifact_sha256"] = art1, sha(tarball)
	e.desire(it)
	e.artifact(art1, tarball)
	e.cp.On("POST", "/api/v1/agent/heartbeat", fakecp.Fail(401, "invalid_token"))
	if got := e.cycle(); len(got) != 0 {
		t.Fatalf("reports %v", got)
	}
	if n := len(e.cp.Calls("GET", "/api/v1/agent/artifacts/"+art1+"/download")); n != 0 {
		t.Fatalf("downloaded %d times with a refused token", n)
	}
}
