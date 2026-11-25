package docker

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

// The expected words come from Python's shlex.split.
func TestSplitMatchesPython(t *testing.T) {
	for _, c := range []struct {
		in   string
		want []string
	}{
		{"a b", []string{"a", "b"}},
		{"\"a b\" c", []string{"a b", "c"}},
		{"'x y' z", []string{"x y", "z"}},
		{"a\\ b", []string{"a b"}},
		{"\"q\\\"x\\n\"", []string{"q\"x\\n"}},
		{"", nil},
		{" sh -c \"echo $HOME\" ", []string{"sh", "-c", "echo $HOME"}},
		{"--port=80 -v", []string{"--port=80", "-v"}},
		{"a''b", []string{"ab"}},
		{"\"\"", []string{""}},
		{"x \"\" y", []string{"x", "", "y"}},
		{"'it'\\''s'", []string{"it's"}},
		{"\"a\\\\b\"", []string{"a\\b"}},
		{"tab\there", []string{"tab", "here"}},
	} {
		got, err := Split(c.in)
		if err != nil || !slices.Equal(got, c.want) {
			t.Errorf("Split(%q) = %q, %v; want %q", c.in, got, err, c.want)
		}
	}
	for _, bad := range []string{"it's", `"open`, `trailing\`} {
		if _, err := Split(bad); err == nil {
			t.Errorf("Split(%q) accepted", bad)
		}
	}
}

func TestPinnedImage(t *testing.T) {
	d := "sha256:" + strings.Repeat("ab", 32)
	for _, c := range []struct{ image, digest, want string }{
		{"nginx", "", "nginx"},
		{"nginx:1.25", d, "nginx@" + d},
		{"registry:5000/team/app:v1", d, "registry:5000/team/app@" + d},
		{"registry:5000/team/app", d, "registry:5000/team/app@" + d},
		{"app@sha256:old", d, "app@" + d},
	} {
		got, err := PinnedImage(c.image, c.digest)
		if err != nil || got != c.want {
			t.Errorf("PinnedImage(%q, %q) = %q, %v; want %q", c.image, c.digest, got, err, c.want)
		}
	}
	if _, err := PinnedImage("nginx", "sha256:short"); err == nil || err.Error() != "Invalid image digest 'sha256:short'." {
		t.Errorf("bad digest: %v", err)
	}
}

// scripted answers each op with a fixed DockerOutput (or error) and records params.
type scripted struct {
	out    map[string]helper.DockerOutput
	err    map[string]error
	params map[string]json.RawMessage
}

func (s *scripted) Call(_ context.Context, op string, params, out any) error {
	if s.params == nil {
		s.params = map[string]json.RawMessage{}
	}
	s.params[op], _ = json.Marshal(params)
	if err := s.err[op]; err != nil {
		return err
	}
	b, _ := json.Marshal(s.out[op])
	return json.Unmarshal(b, out)
}

func TestCheck(t *testing.T) {
	w := runtime.Workload{Key: "app", Spec: runtime.Spec{}}
	for _, c := range []struct {
		out     helper.DockerOutput
		err     error
		status  string
		problem string
	}{
		{out: helper.DockerOutput{OK: true, Output: "true|running|0|\n"}, status: runtime.Healthy},
		{out: helper.DockerOutput{OK: true, Output: "true|running|0|healthy\n"}, status: runtime.Healthy},
		{out: helper.DockerOutput{OK: true, Output: "true|running|0|starting\n"}, status: runtime.Unknown, problem: "container app HEALTHCHECK starting"},
		{out: helper.DockerOutput{OK: true, Output: "true|running|0|unhealthy\n"}, status: runtime.Unhealthy, problem: "container app HEALTHCHECK failed"},
		{out: helper.DockerOutput{OK: true, Output: "true|restarting|137|\n"}, status: runtime.Unhealthy, problem: "container app is not running (restarting, exit 137)"},
		{out: helper.DockerOutput{OK: true, Output: "false|exited|0|\n"}, status: runtime.Unhealthy, problem: "container app is not running (exited, exit 0)"},
		{out: helper.DockerOutput{OK: false, Output: "No such object"}, status: runtime.Missing, problem: "container app not found"},
		{out: helper.DockerOutput{OK: false, Output: "Error response from daemon: No such container: app"}, status: runtime.Missing, problem: "container app not found"},
		{out: helper.DockerOutput{OK: false, Output: "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"}, status: runtime.Unhealthy,
			problem: "container app could not be inspected: Cannot connect to the Docker daemon at unix:///var/run/docker.sock"},
		{err: errors.New("helper down"), status: runtime.Unhealthy, problem: "container app could not be inspected"},
	} {
		h := &scripted{out: map[string]helper.DockerOutput{"docker.health": c.out}, err: map[string]error{"docker.health": c.err}}
		got := Runtime{Helper: h}.Check(context.Background(), w)
		if got.Status != c.status || got.Problem != c.problem {
			t.Errorf("%q: %+v, want %s %q", c.out.Output, got, c.status, c.problem)
		}
	}
}

func TestApplyBuildsTheRunRequest(t *testing.T) {
	h := &scripted{out: map[string]helper.DockerOutput{
		"docker.run": {OK: true}, "docker.health": {OK: true, Output: "true|running|0|starting"},
	}}
	w := runtime.Workload{Key: "app", Spec: runtime.Spec{
		"image": "nginx", "restart": nil, "command": "nginx -g 'daemon off;'",
		"env": map[string]any{"A": "1", "METEORCLOUD_ENDPOINT": "https://own"},
	}, App: &runtime.App{TokenFile: "/var/lib/meteorcli/app-credentials/app-x/token",
		Env: map[string]string{"METEORCLOUD_ENDPOINT": "https://cp", "METEORCLOUD_APP_ID": "a"}}}
	res, err := Runtime{Helper: h}.Apply(context.Background(), w)
	if err != nil || res.Health.Status != runtime.Unknown {
		t.Fatalf("Apply = %+v, %v", res, err)
	}
	var req helper.DockerRunRequest
	_ = json.Unmarshal(h.params["docker.run"], &req)
	if req.Restart != "" || !slices.Equal(req.Command, []string{"nginx", "-g", "daemon off;"}) ||
		req.Env["METEORCLOUD_ENDPOINT"] != "https://own" || req.Env["METEORCLOUD_APP_ID"] != "a" || req.Env["A"] != "1" ||
		req.TokenFile != w.App.TokenFile {
		t.Fatalf("request %+v", req)
	}
	if _, called := h.params["docker.load"]; called {
		t.Error("loaded a tarball for a pulled image")
	}
	if req.PullNever {
		t.Error("a registry image may not be pulled")
	}
}

func TestAnArtifactImageIsNeverPulled(t *testing.T) {
	h := &scripted{out: map[string]helper.DockerOutput{
		"docker.load": {OK: true, Output: "Loaded image: demo/app:1.0\n"},
		"docker.run":  {OK: true}, "docker.health": {OK: true, Output: "true|running|0|"},
	}}
	w := runtime.Workload{Key: "app", Artifact: "/var/lib/meteorcli/artifacts/a1", Spec: runtime.Spec{"image": "demo/app:1.0"}}
	if _, err := (Runtime{Helper: h}).Apply(context.Background(), w); err != nil {
		t.Fatal(err)
	}
	var req helper.DockerRunRequest
	_ = json.Unmarshal(h.params["docker.run"], &req)
	if !req.PullNever || req.Image != "demo/app:1.0" {
		t.Fatalf("request %+v", req)
	}
}

func TestApplyFailures(t *testing.T) {
	for _, c := range []struct {
		name string
		spec runtime.Spec
		art  string
		out  map[string]helper.DockerOutput
		want string
	}{
		{name: "no image", spec: runtime.Spec{}, want: "A docker release needs an image name or a loadable tarball."},
		{name: "run fails", spec: runtime.Spec{"image": "x"}, out: map[string]helper.DockerOutput{"docker.run": {OK: false, Output: "pull access denied"}},
			want: "docker run app failed: pull access denied"},
		{name: "load reports nothing", art: "/a/t.tar", spec: runtime.Spec{}, out: map[string]helper.DockerOutput{"docker.load": {OK: true, Output: "ok"}},
			want: "docker load succeeded but no image name was reported."},
		{name: "bad name", spec: runtime.Spec{"image": "x", "container_name": "../etc"}, want: "Invalid container_name: ../etc"},
	} {
		t.Run(c.name, func(t *testing.T) {
			_, err := Runtime{Helper: &scripted{out: c.out}}.Apply(context.Background(), runtime.Workload{Key: "app", Spec: c.spec, Artifact: c.art})
			if err == nil || err.Error() != c.want {
				t.Fatalf("err = %v, want %q", err, c.want)
			}
		})
	}
}

func TestRemove(t *testing.T) {
	h := &scripted{out: map[string]helper.DockerOutput{"docker.remove": {OK: true}}}
	if err := (Runtime{Helper: h}).Remove(context.Background(), runtime.Workload{Key: "app", Spec: runtime.Spec{}}); err != nil {
		t.Fatal(err)
	}
	if string(h.params["docker.remove"]) != `{"name":"app"}` {
		t.Fatalf("params %s", h.params["docker.remove"])
	}
}

func TestPullFromTheMeteorCloudRegistry(t *testing.T) {
	d := "sha256:" + strings.Repeat("ab", 32)
	running := helper.DockerOutput{OK: true, Output: "true|running|0|"}
	w := runtime.Workload{Key: "web", Spec: runtime.Spec{"image": "registry.example.com/acme/web:1", "image_digest": d}}
	asked := 0
	w.PullAuth = func(context.Context) (runtime.PullAuth, error) {
		asked++
		return runtime.PullAuth{Registry: "registry.example.com", Username: "device", Password: "pw"}, nil
	}

	h := &scripted{out: map[string]helper.DockerOutput{"docker.pull": {OK: true}, "docker.run": {OK: true}, "docker.health": running}}
	if _, err := (Runtime{Helper: h}).Apply(context.Background(), w); err != nil {
		t.Fatal(err)
	}
	var pull helper.DockerPullRequest
	var run helper.DockerRunRequest
	_ = json.Unmarshal(h.params["docker.pull"], &pull)
	_ = json.Unmarshal(h.params["docker.run"], &run)
	if pull.Image != "registry.example.com/acme/web@"+d || pull.Password != "pw" || run.Image != pull.Image || !run.PullNever {
		t.Errorf("pull %+v run %+v", pull, run)
	}

	// On the device already: no credential is asked for.
	h = &scripted{out: map[string]helper.DockerOutput{"docker.has_image": {OK: true}, "docker.run": {OK: true}, "docker.health": running}}
	if _, err := (Runtime{Helper: h}).Apply(context.Background(), w); err != nil || asked != 1 || h.params["docker.pull"] != nil {
		t.Errorf("err %v, asked %d, pull %s", err, asked, h.params["docker.pull"])
	}

	// A failed pull leaves the running container alone.
	h = &scripted{out: map[string]helper.DockerOutput{"docker.pull": {Output: "denied"}}}
	if _, err := (Runtime{Helper: h}).Apply(context.Background(), w); err == nil || !strings.Contains(err.Error(), "denied") {
		t.Errorf("err %v", err)
	}
	if h.params["docker.run"] != nil {
		t.Error("docker run after a failed pull")
	}

	w.Spec = runtime.Spec{"image": "registry.example.com/acme/web:1"}
	if _, err := (Runtime{Helper: h}).Apply(context.Background(), w); err == nil || !strings.Contains(err.Error(), "digest") {
		t.Errorf("a tag was accepted: %v", err)
	}
}

func TestLimit(t *testing.T) {
	for _, v := range []any{json.Number("2000000"), float64(2000000), "2000000"} {
		if n, err := limit(map[string]any{"memory_mb": v}, "memory_mb"); n != 2000000 || err != nil {
			t.Errorf("%v: %d, %v", v, n, err)
		}
	}
	if n, err := limit(map[string]any{"memory_mb": nil}, "memory_mb"); n != 0 || err != nil {
		t.Errorf("null: %d, %v", n, err)
	}
	for _, v := range []any{"x", -1.0, 1.5, 1e300, true} {
		if _, err := limit(map[string]any{"memory_mb": v}, "memory_mb"); err == nil {
			t.Errorf("%v was accepted", v)
		}
	}
}
