package helper

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/localrpc"
)

// deployHost is a fake host with the agent's directories in a temp root.
func deployHost(t *testing.T, f *fakeHost) (Host, string) {
	t.Helper()
	root := t.TempDir()
	h := f.host(filepath.Join(root, "units"))
	h.ArtifactsDir = filepath.Join(root, "artifacts")
	h.ComposeDir = filepath.Join(root, "compose")
	h.AppCredentialsDir = filepath.Join(root, "app-credentials")
	h.TempDir = filepath.Join(root, "tmp")
	for _, d := range []string{h.ArtifactsDir, h.ComposeDir + "/shop", h.AppCredentialsDir + "/shop-abc", h.TempDir} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	return h, root
}

func touch(t *testing.T, path string) string {
	t.Helper()
	if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDockerRun(t *testing.T) {
	var envFile string
	f := &fakeHost{output: "0123\n"}
	h, _ := deployHost(t, f)
	run := h.Run
	h.Run = func(ctx context.Context, name string, args ...string) ([]byte, error) {
		if i := slices.Index(args, "--env-file"); i >= 0 {
			envFile = args[i+1]
			data, _ := os.ReadFile(envFile)
			if string(data) != "SECRET=s3cr3t\n" {
				t.Errorf("env file %q", data)
			}
		}
		return run(ctx, name, args...)
	}
	token := touch(t, filepath.Join(h.AppCredentialsDir, "shop-abc", "token"))
	out, err := call(t, h, "docker.run", DockerRunRequest{
		Name: "shop", Image: "nginx@sha256:" + strings.Repeat("a", 64), Restart: "unless-stopped",
		Ports: []string{"8080:80", "127.0.0.1:9000:9000/udp"}, Env: map[string]string{"SECRET": "s3cr3t"},
		TokenFile: token, Command: []string{"--flag", "-x"},
	})
	if err != nil {
		t.Fatal(err)
	}
	var reply DockerOutput
	_ = json.Unmarshal(out, &reply)
	if !reply.OK {
		t.Fatalf("reply %+v", reply)
	}
	if !slices.Equal(f.calls[0], []string{"docker", "rm", "-f", "shop"}) {
		t.Errorf("first ran %v", f.calls[0])
	}
	want := []string{"docker", "run", "-d", "--name", "shop", "--restart", "unless-stopped", "-p", "8080:80",
		"-p", "127.0.0.1:9000:9000/udp", "--env-file", envFile, "-v", token + ":/run/secrets/meteorcloud-token:ro",
		"nginx@sha256:" + strings.Repeat("a", 64), "--flag", "-x"}
	if !slices.Equal(f.calls[1], want) {
		t.Errorf("ran %v\nwant %v", f.calls[1], want)
	}
	if strings.Contains(strings.Join(f.calls[1], " "), "s3cr3t") {
		t.Error("an env value is on the command line")
	}
	if _, err := os.Stat(envFile); !os.IsNotExist(err) {
		t.Error("env file left behind")
	}
}

func TestDockerRunRejects(t *testing.T) {
	f := &fakeHost{}
	h, root := deployHost(t, f)
	outside := touch(t, filepath.Join(root, "token"))
	good := DockerRunRequest{Name: "app", Image: "nginx"}
	for name, mutate := range map[string]func(r *DockerRunRequest){
		"option as name":  func(r *DockerRunRequest) { r.Name = "-privileged" },
		"option as image": func(r *DockerRunRequest) { r.Image = "--privileged" },
		"restart policy":  func(r *DockerRunRequest) { r.Restart = "sometimes" },
		"option as port":  func(r *DockerRunRequest) { r.Ports = []string{"--privileged"} },
		"env name":        func(r *DockerRunRequest) { r.Env = map[string]string{"A=B": "1"} },
		"env newline":     func(r *DockerRunRequest) { r.Env = map[string]string{"A": "1\nB=2"} },
		"token outside":   func(r *DockerRunRequest) { r.TokenFile = outside },
		"token not a token": func(r *DockerRunRequest) {
			r.TokenFile = touch(t, filepath.Join(h.AppCredentialsDir, "shop-abc", "meta.json"))
		},
		"token by relative path": func(r *DockerRunRequest) { r.TokenFile = "app-credentials/shop-abc/token" },
	} {
		r := good
		mutate(&r)
		if _, err := call(t, h, "docker.run", r); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: err = %v", name, err)
		}
	}
	if len(f.calls) != 0 {
		t.Errorf("ran %v for refused requests", f.calls)
	}
}

func TestDockerLoadOnlyFromArtifacts(t *testing.T) {
	f := &fakeHost{output: "Loaded image: demo/app:1.0\n"}
	h, root := deployHost(t, f)
	tarball := touch(t, filepath.Join(h.ArtifactsDir, "a1"))
	if _, err := call(t, h, "docker.load", DockerPathRequest{Path: tarball}); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(f.calls[0], []string{"docker", "load", "-i", tarball}) {
		t.Errorf("ran %v", f.calls[0])
	}
	link := filepath.Join(h.ArtifactsDir, "link")
	_ = os.Symlink(touch(t, filepath.Join(root, "shadow")), link)
	_ = os.MkdirAll(filepath.Join(h.ArtifactsDir, "sub"), 0o700)
	for _, p := range []string{filepath.Join(root, "shadow"), link, h.ArtifactsDir + "/../shadow", filepath.Join(h.ArtifactsDir, "sub"),
		filepath.Join(h.ArtifactsDir, "missing")} {
		if _, err := call(t, h, "docker.load", DockerPathRequest{Path: p}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: err = %v", p, err)
		}
	}
}

func TestDockerHealthAndRemove(t *testing.T) {
	f := &fakeHost{output: "true|running|0|\n"}
	h, _ := deployHost(t, f)
	if _, err := call(t, h, "docker.health", DockerNameRequest{Name: "app"}); err != nil {
		t.Fatal(err)
	}
	if _, err := call(t, h, "docker.remove", DockerNameRequest{Name: "app"}); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(f.calls[0], []string{"docker", "inspect", "-f", DockerHealthTemplate, "app"}) ||
		!slices.Equal(f.calls[1], []string{"docker", "rm", "-f", "app"}) {
		t.Errorf("ran %v", f.calls)
	}
	for _, op := range []string{"docker.health", "docker.remove"} {
		if _, err := call(t, h, op, DockerNameRequest{Name: "-f"}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s accepted an option as name: %v", op, err)
		}
	}
}

func TestCompose(t *testing.T) {
	f := &fakeHost{output: "web\n"}
	h, root := deployHost(t, f)
	dir := filepath.Join(h.ComposeDir, "shop")
	file := touch(t, filepath.Join(dir, "compose.yaml"))
	env := touch(t, filepath.Join(dir, ".env.meteorcloud"))
	override := touch(t, filepath.Join(dir, "compose.meteorcloud.json"))
	for _, c := range []struct {
		op   string
		req  ComposeRequest
		want []string
	}{
		{"compose.services", ComposeRequest{Project: "shop", File: file, EnvFile: env},
			[]string{"docker", "compose", "-p", "shop", "-f", file, "--env-file", env, "config", "--services"}},
		{"compose.up", ComposeRequest{Project: "shop", File: file, OverrideFile: override, ForceRecreate: true},
			[]string{"docker", "compose", "-p", "shop", "-f", file, "-f", override, "up", "-d", "--remove-orphans", "--force-recreate"}},
		{"compose.ps", ComposeRequest{Project: "shop"}, []string{"docker", "compose", "-p", "shop", "ps", "-a", "--format", "json"}},
		{"compose.down", ComposeRequest{Project: "shop"}, []string{"docker", "compose", "-p", "shop", "down", "--remove-orphans"}},
	} {
		f.calls = nil
		if _, err := call(t, h, c.op, c.req); err != nil {
			t.Fatalf("%s: %v", c.op, err)
		}
		if !slices.Equal(f.calls[0], c.want) {
			t.Errorf("%s ran %v\nwant %v", c.op, f.calls[0], c.want)
		}
	}
	other := touch(t, filepath.Join(root, "evil.yaml"))
	_ = os.MkdirAll(filepath.Join(h.ComposeDir, "other"), 0o700)
	otherProject := touch(t, filepath.Join(h.ComposeDir, "other", "compose.yaml"))
	for name, req := range map[string]ComposeRequest{
		"file outside":           {Project: "shop", File: other},
		"another project's file": {Project: "shop", File: otherProject},
		"env outside":            {Project: "shop", File: file, EnvFile: other},
		"bad project":            {Project: "-shop", File: file},
		"capital project":        {Project: "Shop", File: file},
		"no file":                {Project: "shop"},
	} {
		if _, err := call(t, h, "compose.up", req); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%s: err = %v", name, err)
		}
	}
}

func TestSystemdControl(t *testing.T) {
	f := &fakeHost{}
	h, _ := deployHost(t, f)
	unitFile := filepath.Join(h.UnitDir, "demo.service")
	_ = os.MkdirAll(h.UnitDir, 0o755)
	touch(t, unitFile)
	out, err := call(t, h, "systemd.control", SystemdControlRequest{Unit: "demo.service", Action: "is_active"})
	if err != nil || string(out) != `{"active":true}` {
		t.Fatalf("is_active = %s, %v", out, err)
	}
	f.fail = "is-active"
	if out, _ := call(t, h, "systemd.control", SystemdControlRequest{Unit: "demo.service", Action: "is_active"}); string(out) != `{"active":false}` {
		t.Errorf("inactive unit: %s", out)
	}
	f.fail = ""
	if _, err := call(t, h, "systemd.control", SystemdControlRequest{Unit: "demo.service", Action: "remove"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(unitFile); !os.IsNotExist(err) {
		t.Error("unit file kept")
	}
	for _, r := range []SystemdControlRequest{
		{Unit: "meteorcli.service", Action: "restart"}, {Unit: "meteorcli-helper.service", Action: "remove"},
		{Unit: "demo", Action: "restart"}, {Unit: "-x.service", Action: "restart"}, {Unit: "demo.service", Action: "mask"},
	} {
		if _, err := call(t, h, "systemd.control", r); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%+v: err = %v", r, err)
		}
	}
}

func TestDeployAuditsHoldNoSecrets(t *testing.T) {
	raw, _ := json.Marshal(DockerRunRequest{Name: "a", Image: "i", Env: map[string]string{"K": "secret-value"}})
	if got, _ := json.Marshal(auditDockerRun(raw)); strings.Contains(string(got), "secret-value") {
		t.Fatalf("audit holds an env value: %s", got)
	}
}

func TestDockerRunPullNever(t *testing.T) {
	f := &fakeHost{}
	h, _ := deployHost(t, f)
	if _, err := call(t, h, "docker.run", DockerRunRequest{Name: "app", Image: "demo/app:1.0", PullNever: true}); err != nil {
		t.Fatal(err)
	}
	want := []string{"docker", "run", "-d", "--name", "app", "--pull", "never", "demo/app:1.0"}
	if !slices.Equal(f.calls[1], want) {
		t.Fatalf("ran %v\nwant %v", f.calls[1], want)
	}
}

func TestDockerRunKeepsTheContainerWhenPreparationFails(t *testing.T) {
	f := &fakeHost{}
	h, root := deployHost(t, f)
	h.TempDir = filepath.Join(root, "no-such-dir")
	if _, err := call(t, h, "docker.run", DockerRunRequest{Name: "app", Image: "nginx", Env: map[string]string{"A": "1"}}); err == nil {
		t.Fatal("docker.run without an env file succeeded")
	}
	if len(f.calls) != 0 {
		t.Fatalf("ran %v: the running container was removed before anything could start", f.calls)
	}
}

func TestSystemdRemoveKeepsAUnitThatDidNotStop(t *testing.T) {
	f := &fakeHost{fail: "disable"}
	h, _ := deployHost(t, f)
	unitFile := filepath.Join(h.UnitDir, "demo.service")
	_ = os.MkdirAll(h.UnitDir, 0o755)
	touch(t, unitFile)
	if _, err := call(t, h, "systemd.control", SystemdControlRequest{Unit: "demo.service", Action: "remove"}); err == nil {
		t.Fatal("remove succeeded although the unit did not stop")
	}
	if _, err := os.Stat(unitFile); err != nil {
		t.Fatal("the unit file was deleted")
	}
	// A unit that was never installed: its disable failing is fine.
	if _, err := call(t, h, "systemd.control", SystemdControlRequest{Unit: "gone.service", Action: "remove"}); err != nil {
		t.Fatal(err)
	}
}

func TestComposeParsedOutputIsStdoutOnly(t *testing.T) {
	f := &fakeHost{}
	h, _ := deployHost(t, f)
	file := touch(t, filepath.Join(h.ComposeDir, "shop", "compose.yaml"))
	h.Stream = func(_ context.Context, w io.Writer, name string, args ...string) error {
		_, err := w.Write([]byte("web\n")) // the warning went to stderr, which Stream does not pass on
		return err
	}
	out, err := call(t, h, "compose.services", ComposeRequest{Project: "shop", File: file})
	var reply DockerOutput
	_ = json.Unmarshal(out, &reply)
	if err != nil || !reply.OK || reply.Output != "web\n" {
		t.Fatalf("reply %+v, %v", reply, err)
	}
	h.Stream = func(context.Context, io.Writer, string, ...string) error {
		return errors.New("docker compose config: exit status 15: yaml: line 2: bad")
	}
	out, _ = call(t, h, "compose.services", ComposeRequest{Project: "shop", File: file})
	_ = json.Unmarshal(out, &reply)
	if reply.OK || !strings.Contains(reply.Output, "yaml: line 2: bad") {
		t.Fatalf("failure reply %+v", reply)
	}
}
