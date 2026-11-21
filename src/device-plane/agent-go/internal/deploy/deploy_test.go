package deploy

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/heartbeat"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/signing"
)

const (
	rel1 = "11111111-1111-1111-1111-111111111111"
	rel2 = "22222222-2222-2222-2222-222222222222"
	art1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
	art2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)

var tarball = []byte("docker save tarball (simulated)\n")

func dockerItem(release, version string, revision int, spec map[string]any) map[string]any {
	return map[string]any{
		"workload_key": "demo-app", "kind": "docker", "release_id": release, "release_name": "demo",
		"release_version": version, "artifact_id": nil, "revision": revision, "artifact_sha256": nil, "spec": spec,
	}
}

func TestDockerTarballDeploysThenOnlyReportsHealth(t *testing.T) {
	e := newEnv(t)
	it := dockerItem(rel1, "1.0.0", 1, map[string]any{"container_name": "demo-app", "ports": []any{"8080:80"}, "restart": "unless-stopped"})
	it["artifact_id"], it["artifact_sha256"] = art1, sha(tarball)
	e.desire(it)
	e.artifact(art1, tarball)

	got := e.cycle()["demo-app"]
	if got.status() != statusInSync || got.health() != "healthy" || got["installed_version"] != "1.0.0" || got["last_error"] != nil {
		t.Fatalf("report %v", got)
	}
	if _, ok := got["last_error"]; !ok {
		t.Error("in_sync must send last_error null to clear an old error")
	}
	if p := e.progress(); !slices.Equal(p, []int{0, 99, 100}) {
		t.Errorf("progress %v, want [0 99 100]", p)
	}
	var run helper.DockerRunRequest
	e.host.last(t, "docker.run", &run)
	if run.Name != "demo-app" || run.Image != "demo/app:1.0" || run.Restart != "unless-stopped" || !slices.Equal(run.Ports, []string{"8080:80"}) {
		t.Errorf("docker.run %+v", run)
	}
	var load helper.DockerPathRequest
	e.host.last(t, "docker.load", &load)
	if load.Path != filepath.Join(e.d.Paths.ArtifactsDir, art1) {
		t.Errorf("docker.load %s", load.Path)
	}
	if _, err := os.Stat(filepath.Join(e.d.State.Dir(), signing.PinsFile)); err != nil {
		t.Errorf("signing key not pinned: %v", err)
	}

	// Next cycle: nothing changed, so no download and no new container.
	downloads := len(e.cp.Calls("GET", "/api/v1/agent/artifacts/"+art1+"/download"))
	again := e.cycle()["demo-app"]
	if again.status() != statusInSync || again.health() != "healthy" {
		t.Fatalf("second report %v", again)
	}
	if e.host.count("docker.run") != 1 || len(e.cp.Calls("GET", "/api/v1/agent/artifacts/"+art1+"/download")) != downloads {
		t.Errorf("re-applied an unchanged release: %v", e.host.calls)
	}

	// The container was removed by hand: it is installed again from the cached artifact.
	delete(e.host.containers, "demo-app")
	if e.cycle()["demo-app"].status() != statusInSync || e.host.count("docker.run") != 2 {
		t.Errorf("missing container not reinstalled: %v", e.host.calls)
	}
	if len(e.cp.Calls("GET", "/api/v1/agent/artifacts/"+art1+"/download")) != downloads {
		t.Error("cached artifact downloaded again")
	}
}

func TestOutcomesAreSentAtOnceAndKeptUntilAccepted(t *testing.T) {
	e := newEnv(t)
	e.desire(dockerItem(rel1, "1", 1, map[string]any{"image": "nginx"}))
	e.cycle()
	if len(e.pending()) != 0 {
		t.Fatalf("an accepted outcome stayed in the outbox: %v", e.pending())
	}
	calls := e.cp.Calls("POST", "/api/v1/agent/heartbeat")
	last := calls[len(calls)-1].Body
	if len(last) != 1 || last["software_report"] == nil {
		t.Fatalf("outcome heartbeat body %v, want only software_report", last)
	}
	// Health-only reports wait for the next heartbeat.
	e.cycle()
	if r := e.pending()["demo-app"]; r.status() != statusInSync {
		t.Fatalf("health report not in the outbox: %v", e.pending())
	}
	// When the server does not take the outcome, the outbox keeps it.
	e.cp.On("POST", "/api/v1/agent/heartbeat", fakecp.Fail(503, "unavailable"))
	e.desire(dockerItem(rel2, "2", 2, map[string]any{"image": "nginx:2"}))
	e.cycle()
	if r := e.pending()["demo-app"]; r["installed_release_id"] != rel2 {
		t.Fatalf("outcome lost when the send failed: %v", e.pending())
	}
}

func TestRefusals(t *testing.T) {
	tests := []struct {
		name  string
		setup func(e *env)
		want  string
	}{
		{name: "unknown kind", setup: func(e *env) {
			it := dockerItem(rel1, "1", 1, map[string]any{})
			it["kind"] = "vm"
			e.desire(it)
		}, want: "Unsupported release kind: 'vm'"},
		{name: "unsigned", setup: func(e *env) {
			e.cp.On("GET", "/api/v1/agent/software", fakecp.OK([]any{dockerItem(rel1, "1", 1, map[string]any{"image": "nginx"})}))
		}, want: "Desired software is not signed; refusing to install it."},
		{name: "tampered", setup: func(e *env) {
			it := signed(e.t, dockerItem(rel1, "1", 1, map[string]any{"image": "nginx"}))
			it["spec"] = map[string]any{"image": "evil"}
			e.cp.On("GET", "/api/v1/agent/software", fakecp.OK([]any{it}))
		}, want: "Desired software signature does not verify; refusing to install it."},
		{name: "artifact does not match the signed checksum", setup: func(e *env) {
			it := dockerItem(rel1, "1", 1, map[string]any{})
			it["artifact_id"], it["artifact_sha256"] = art1, sha([]byte("the real one"))
			e.desire(it)
			e.cp.On("GET", "/api/v1/agent/artifacts/"+art1+"/download", fakecp.Reply{Status: 200, Raw: tarball})
		}, want: "Artifact " + art1 + " does not match the signed checksum; refusing to install it."},
		{name: "server checksum mismatch", setup: func(e *env) {
			it := dockerItem(rel1, "1", 1, map[string]any{})
			it["artifact_id"] = art1
			e.desire(it)
			e.cp.On("GET", "/api/v1/agent/artifacts/"+art1+"/download",
				fakecp.Reply{Status: 200, Raw: tarball, Header: map[string]string{"X-Checksum-SHA256": sha([]byte("x"))}})
		}, want: "Downloaded artifact failed SHA-256 verification."},
		{name: "systemd without artifact", setup: func(e *env) {
			it := dockerItem(rel1, "1", 1, map[string]any{})
			it["kind"] = "systemd"
			e.desire(it)
		}, want: "systemd release is missing an artifact_id."},
		{name: "docker without image", setup: func(e *env) { e.desire(dockerItem(rel1, "1", 1, map[string]any{})) },
			want: "docker release needs an image name or a tarball artifact."},
		{name: "no compose on the device", setup: func(e *env) {
			e.d.Host.Run = func(context.Context, string, ...string) bool { return false }
			it := dockerItem(rel1, "1", 1, map[string]any{})
			it["kind"], it["artifact_id"] = "docker_compose", art1
			e.desire(it)
			e.artifact(art1, []byte("services: {}\n"))
		}, want: "This device does not support Docker Compose."},
		{name: "systemd binding", setup: func(e *env) {
			it := dockerItem(rel1, "1", 1, map[string]any{"meteorcloud": map[string]any{"application": "app-1"}})
			it["kind"], it["artifact_id"] = "systemd", art1
			e.desire(it)
		}, want: "A MeteorCloud application binding needs a docker or docker_compose release."},
		{name: "container exits right away", setup: func(e *env) {
			e.host.broken["nginx"] = true
			e.desire(dockerItem(rel1, "1", 1, map[string]any{"image": "nginx"}))
		}, want: "container demo-app is not running (exited, exit 1) after apply."},
		{name: "bad command quoting", setup: func(e *env) {
			e.desire(dockerItem(rel1, "1", 1, map[string]any{"image": "nginx", "command": "sh -c 'echo"}))
		}, want: "No closing quotation"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t)
			tc.setup(e)
			got := e.cycle()["demo-app"]
			if got.status() != statusFailed || got.lastError() != tc.want || got.health() != "unhealthy" {
				t.Fatalf("report %v, want failed %q", got, tc.want)
			}
		})
	}
}

func TestUnsignedAcceptedWhenServerDoesNotSign(t *testing.T) {
	e := newEnv(t)
	e.cp.On("GET", "/api/v1/agent/signing-keys", fakecp.Fail(404, "not_found"))
	e.cp.On("GET", "/api/v1/agent/software", fakecp.OK([]any{dockerItem(rel1, "1", 1, map[string]any{"image": "nginx", "image_digest": "sha256:" + strings.Repeat("a", 64)})}))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync {
		t.Fatalf("report %v", got)
	}
	var run helper.DockerRunRequest
	e.host.last(t, "docker.run", &run)
	if run.Image != "nginx@sha256:"+strings.Repeat("a", 64) {
		t.Errorf("image not pinned to its digest: %s", run.Image)
	}
}

func TestOlderRevisionIsRefused(t *testing.T) {
	e := newEnv(t)
	e.desire(dockerItem(rel2, "2.0", 5, map[string]any{"image": "nginx:2"}))
	if e.cycle()["demo-app"].status() != statusInSync {
		t.Fatal("first apply failed")
	}
	e.desire(dockerItem(rel1, "1.0", 4, map[string]any{"image": "nginx:1"}))
	got := e.cycle()["demo-app"]
	if got.lastError() != "Desired revision 4 is older than applied revision 5; refusing rollback." {
		t.Fatalf("report %v", got)
	}
}

func TestBrokenReleaseRollsBack(t *testing.T) {
	e := newEnv(t)
	gate := map[string]any{"timeout_seconds": 6, "stable_seconds": 2}
	e.desire(dockerItem(rel1, "1.0", 1, map[string]any{"image": "demo:1", "health_check": gate}))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync || got.health() != "healthy" {
		t.Fatalf("v1 %v", got)
	}

	e.host.broken["demo:2"] = true
	e.desire(dockerItem(rel2, "2.0", 2, map[string]any{"image": "demo:2", "health_check": gate}))
	got := e.cycle()["demo-app"]
	if got.status() != statusRolledBack || got["installed_release_id"] != rel1 || got["installed_version"] != "1.0" ||
		got.health() != "healthy" || !strings.HasPrefix(got.lastError(), "Rolled back to 1.0: container demo-app is not running") {
		t.Fatalf("report %v", got)
	}
	if e.host.containers["demo-app"] != "demo:1" {
		t.Errorf("running %s, want demo:1", e.host.containers["demo-app"])
	}

	// The failed release is not tried again while it stays desired.
	runs := e.host.count("docker.run")
	again := e.cycle()["demo-app"]
	if again.status() != statusRolledBack || e.host.count("docker.run") != runs {
		t.Fatalf("retried the failed release: %v, %v", again, e.host.calls)
	}

	// A new release is applied again.
	e.desire(dockerItem("33333333-3333-3333-3333-333333333333", "3.0", 3, map[string]any{"image": "demo:3", "health_check": gate}))
	if got := e.cycle()["demo-app"]; got.status() != statusInSync || got["installed_version"] != "3.0" {
		t.Fatalf("v3 %v", got)
	}
}

func TestHealthGateFailsAfterApplyAndRollbackFails(t *testing.T) {
	gate := map[string]any{"timeout_seconds": 4, "stable_seconds": 0, "http_url": "http://127.0.0.1:8080/health"}

	// v2 runs but its URL never answers: v1 comes back (the rollback has no gate of its own).
	e := newEnv(t)
	e.desire(dockerItem(rel1, "1.0", 1, map[string]any{"image": "demo:1", "health_check": gate}))
	e.cycle()
	e.m.probe = func(context.Context, string) string { return "http://127.0.0.1:8080/health answered HTTP 503" }
	e.desire(dockerItem(rel2, "2.0", 2, map[string]any{"image": "demo:2", "health_check": gate}))
	got := e.cycle()["demo-app"]
	if got.status() != statusRolledBack || got.lastError() != "Rolled back to 1.0: Health check failed: http://127.0.0.1:8080/health answered HTTP 503" {
		t.Fatalf("report %v", got)
	}

	// The rollback's own apply fails too: both problems are reported.
	e = newEnv(t)
	e.desire(dockerItem(rel1, "1.0", 1, map[string]any{"image": "demo:1", "health_check": gate}))
	e.cycle()
	e.host.broken["demo:1"], e.host.broken["demo:2"] = true, true
	e.desire(dockerItem(rel2, "2.0", 2, map[string]any{"image": "demo:2", "health_check": gate}))
	got = e.cycle()["demo-app"]
	// The container exits at once, so the apply itself fails (before the gate).
	want := "container demo-app is not running (exited, exit 1) after apply.; rolling back to 1.0 also failed: " +
		"container demo-app is not running (exited, exit 1) after apply."
	if got.status() != statusFailed || got.lastError() != want {
		t.Fatalf("report %v\nwant last_error %q", got, want)
	}
}

func TestNoRollbackWhenTheGateSaysSo(t *testing.T) {
	e := newEnv(t)
	gate := map[string]any{"timeout_seconds": 2, "rollback": false}
	e.desire(dockerItem(rel1, "1.0", 1, map[string]any{"image": "demo:1", "health_check": gate}))
	e.cycle()
	e.host.broken["demo:2"] = true
	e.desire(dockerItem(rel2, "2.0", 2, map[string]any{"image": "demo:2", "health_check": gate}))
	if got := e.cycle()["demo-app"]; got.status() != statusFailed {
		t.Fatalf("report %v", got)
	}
}

func composeBundle(t *testing.T) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range map[string]string{
		"docker-compose.yml": "services:\n  web:\n    image: nginx\n    volumes: [./config:/etc/app]\n",
		"config/app.conf":    "x=1\n",
	} {
		w, _ := zw.Create(name)
		_, _ = w.Write([]byte(body))
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestComposeAppDeploys(t *testing.T) {
	e := newEnv(t)
	bundle := composeBundle(t)
	it := map[string]any{
		"workload_key": "shop", "kind": "docker_compose", "release_id": rel1, "release_name": "shop",
		"release_version": "1.2.0", "artifact_id": art1, "revision": 1, "artifact_sha256": sha(bundle),
		"spec": map[string]any{"project_name": "shop", "env": map[string]any{"TAG": "1.2.0", "REPLICAS": 2}},
	}
	e.desire(it)
	e.artifact(art1, bundle)
	got := e.cycle()["shop"]
	if got.status() != statusInSync || got.health() != "healthy" || got["installed_version"] != "1.2.0" {
		t.Fatalf("report %v", got)
	}
	var up helper.ComposeRequest
	e.host.last(t, "compose.up", &up)
	dir := filepath.Join(e.d.Paths.ComposeDir(), "shop")
	if up.Project != "shop" || up.File != filepath.Join(dir, "docker-compose.yml") || up.EnvFile != filepath.Join(dir, ".env.meteorcloud") || up.OverrideFile != "" {
		t.Errorf("compose.up %+v", up)
	}
	if env, _ := os.ReadFile(up.EnvFile); string(env) != "REPLICAS='2'\nTAG='1.2.0'\n" {
		t.Errorf("env file %q", env)
	}
	if st, err := os.Stat(filepath.Join(dir, "config", "app.conf")); err != nil || st.Mode().Perm() != 0o644 {
		t.Errorf("bundled file: %v %v", st, err)
	}
}

func TestBoundComposeAppGetsCredentials(t *testing.T) {
	e := newEnv(t)
	bundle := composeBundle(t)
	e.cp.On("POST", "/api/v1/agent/applications/credentials", fakecp.Created(map[string]any{
		"token": "app_secret_token", "credential_id": "c1", "expires_at": "2999-01-01T00:00:00Z",
		"refresh_after": "2998-01-01T00:00:00Z", "endpoint": "https://data.example", "application": "app-1",
		"application_id": "a1", "organization_id": "o1", "device_id": agenttest.DeviceID, "environment": "production",
	}))
	e.desire(map[string]any{
		"workload_key": "shop", "kind": "docker_compose", "release_id": rel1, "release_name": "shop",
		"release_version": "1.0", "artifact_id": art1, "revision": 1, "artifact_sha256": sha(bundle),
		"spec": map[string]any{"meteorcloud": map[string]any{"application": "app-1"}},
	})
	e.artifact(art1, bundle)
	if got := e.cycle()["shop"]; got.status() != statusInSync {
		t.Fatalf("report %v", got)
	}
	var up helper.ComposeRequest
	e.host.last(t, "compose.up", &up)
	override, err := os.ReadFile(up.OverrideFile)
	if err != nil {
		t.Fatal(err)
	}
	creds := e.d.Paths.AppCredentialsDir()
	for _, want := range []string{`"web"`, `"worker"`, `"METEORCLOUD_ENDPOINT": "https://data.example"`, creds, ":/run/secrets/meteorcloud-token:ro"} {
		if !strings.Contains(string(override), want) {
			t.Errorf("override lacks %s:\n%s", want, override)
		}
	}
	if strings.Contains(string(override), "app_secret_token") {
		t.Error("the token is in the override file")
	}
	// Second cycle: still valid, no new token, no new apply.
	e.cycle()
	if n := len(e.cp.Calls("POST", "/api/v1/agent/applications/credentials")); n != 1 || e.host.count("compose.up") != 1 {
		t.Errorf("credentials minted %d times, compose up %d times", n, e.host.count("compose.up"))
	}
	// The token file is lost: the workload is applied again, recreating the containers.
	_ = os.RemoveAll(creds)
	e.cycle()
	e.host.last(t, "compose.up", &up)
	if e.host.count("compose.up") != 2 || !up.ForceRecreate {
		t.Errorf("compose up %d times, force_recreate %v", e.host.count("compose.up"), up.ForceRecreate)
	}
}

func TestBoundDockerWithoutEndpointFails(t *testing.T) {
	e := newEnv(t)
	e.cp.On("POST", "/api/v1/agent/applications/credentials", fakecp.Created(map[string]any{
		"token": "t", "credential_id": "c1", "expires_at": "2999-01-01T00:00:00Z", "refresh_after": "2998-01-01T00:00:00Z",
		"endpoint": nil, "application": "app-1", "application_id": "a1", "organization_id": "o1", "device_id": "d-1", "environment": "production",
	}))
	e.desire(dockerItem(rel1, "1", 1, map[string]any{"image": "nginx", "meteorcloud": map[string]any{"application": "app-1"}}))
	got := e.cycle()["demo-app"]
	if !strings.HasPrefix(got.lastError(), "The control plane has no data plane address") {
		t.Fatalf("report %v", got)
	}
}

func TestSystemdUnitDeploys(t *testing.T) {
	e := newEnv(t)
	root := e.d.Host.Root
	if err := os.MkdirAll(filepath.Join(root, "run", "systemd", "system"), 0o755); err != nil {
		t.Fatal(err)
	}
	e.d.Host.LookPath = func(n string) (string, error) {
		if n == "systemctl" {
			return "/usr/bin/systemctl", nil
		}
		return "", errors.New("no")
	}
	unit := []byte("[Unit]\nDescription=demo\n[Service]\nExecStart=/bin/true\n")
	it := dockerItem(rel1, "1", 1, map[string]any{"unit_name": "demo.service"})
	it["kind"], it["artifact_id"], it["artifact_sha256"] = "systemd", art1, sha(unit)
	e.desire(it)
	e.artifact(art1, unit)
	if got := e.cycle()["demo-app"]; got.status() != statusInSync || got.health() != "healthy" {
		t.Fatalf("report %v", got)
	}
	if e.host.units["demo.service"] != string(unit) {
		t.Errorf("units %v", e.host.units)
	}
	e.host.broken["demo.service"] = true
	if got := e.cycle()["demo-app"]; got.status() != statusInSync || got.health() != "unhealthy" || got.lastError() != "systemd unit demo.service is not active" {
		t.Fatalf("stopped unit: %v", got)
	}
}

func TestRefusedTokenGoesToIdentity(t *testing.T) {
	e := newEnv(t)
	e.cp.On("GET", "/api/v1/agent/software", fakecp.Fail(401, "invalid_token"))
	if got := e.cycle(); len(got) != 0 {
		t.Fatalf("reports %v", got)
	}
	if _, err := e.d.Identity.Token(); err == nil {
		t.Error("the refused token is still in use")
	}
}

func TestAHungPullDoesNotDelayHeartbeats(t *testing.T) {
	cp := fakecp.New(t)
	d := agenttest.Deps(t, cp, nil, nil)
	d.State = newEnv(t).d.State
	cp.On("POST", "/api/v1/agent/heartbeat", fakecp.OK(map[string]any{"device_id": agenttest.DeviceID, "status": "online",
		"heartbeat_interval_seconds": 1}))
	cp.On("GET", "/api/v1/agent/signing-keys", signingKeys())
	cp.On("GET", "/api/v1/agent/software", fakecp.OK([]any{signed(t, dockerItem(rel1, "1", 1, map[string]any{"image": "huge:1"}))}))
	host := newFakeHost()
	host.block = make(chan struct{}) // docker run never returns: a pull that hangs
	defer close(host.block)

	agenttest.Start(t, &heartbeat.Module{}, d)
	agenttest.Start(t, &Module{Helper: host}, d)
	deadline := time.Now().Add(10 * time.Second)
	for host.count("docker.run") == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if host.count("docker.run") == 0 {
		t.Fatal("deploy never started the pull")
	}
	before := len(cp.Calls("POST", "/api/v1/agent/heartbeat"))
	// The heartbeat interval is never below 5 s; another beat must come while the pull hangs.
	cp.Wait(t, "POST", "/api/v1/agent/heartbeat", before+1, 8*time.Second)
	if h, _ := d.Health.Get("deploy"); h.State != string(agent.StateRunning) {
		t.Errorf("deploy health %v", h)
	}
}
