package compose

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

func zipOf(t *testing.T, files map[string]string, symlink string) string {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range files {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = w.Write([]byte(body))
	}
	if symlink != "" {
		h := &zip.FileHeader{Name: symlink}
		h.SetMode(os.ModeSymlink | 0o777)
		w, _ := zw.CreateHeader(h)
		_, _ = w.Write([]byte("/etc/shadow"))
	}
	_ = zw.Close()
	path := filepath.Join(t.TempDir(), "bundle")
	_ = os.WriteFile(path, buf.Bytes(), 0o600)
	return path
}

func TestMaterialize(t *testing.T) {
	plain := filepath.Join(t.TempDir(), "artifact")
	_ = os.WriteFile(plain, []byte("services: {}\n"), 0o600)
	for _, c := range []struct {
		name     string
		artifact string
		want     string // compose file name, or error text
		fail     bool
	}{
		{name: "plain file", artifact: plain, want: "compose.yaml"},
		{name: "zip", artifact: zipOf(t, map[string]string{"compose.yml": "x", "app/conf": "y"}, ""), want: "compose.yml"},
		{name: "preferred name", artifact: zipOf(t, map[string]string{"compose.yaml": "x", "docker-compose.yml": "y"}, ""), want: "docker-compose.yml"},
		{name: "no compose file", artifact: zipOf(t, map[string]string{"readme": "x"}, ""), fail: true,
			want: "Compose zip must contain docker-compose.yml, docker-compose.yaml, compose.yml, or compose.yaml."},
		{name: "zip slip", artifact: zipOf(t, map[string]string{"../../evil": "x"}, ""), fail: true,
			want: `Compose zip entry "../../evil" is outside the bundle.`},
		{name: "absolute", artifact: zipOf(t, map[string]string{"/etc/evil": "x"}, ""), fail: true,
			want: `Compose zip entry "/etc/evil" is outside the bundle.`},
		{name: "missing", artifact: "/nonexistent/artifact", fail: true, want: "Compose artifact not found: /nonexistent/artifact"},
	} {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			got, err := materialize(c.artifact, dir)
			if c.fail {
				if err == nil || err.Error() != c.want {
					t.Fatalf("err = %v, want %q", err, c.want)
				}
				return
			}
			if err != nil || got != filepath.Join(dir, c.want) {
				t.Fatalf("materialize = %q, %v", got, err)
			}
		})
	}
}

func TestSymlinksInABundleAreNotCreated(t *testing.T) {
	dir := t.TempDir()
	if _, err := materialize(zipOf(t, map[string]string{"compose.yml": "x"}, "secret"), dir); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(filepath.Join(dir, "secret")); !os.IsNotExist(err) {
		t.Fatalf("symlink entry created: %v", err)
	}
}

func TestParsePS(t *testing.T) {
	lines := `{"Service":"web","State":"running"}` + "\n" + `{"Service":"db","State":"running","Health":"healthy"}`
	if rows := parsePS(lines); len(rows) != 2 || rows[1].Health != "healthy" {
		t.Fatalf("lines: %+v", rows)
	}
	if rows := parsePS(`[{"Service":"web","State":"exited","ExitCode":3}]`); len(rows) != 1 || rows[0].ExitCode != 3 {
		t.Fatalf("array: %+v", rows)
	}
	if rows := parsePS("garbage"); rows != nil {
		t.Fatalf("garbage: %+v", rows)
	}
}

type scripted struct {
	out    map[string]helper.DockerOutput
	params map[string]json.RawMessage
}

func (s *scripted) Call(_ context.Context, op string, params, out any) error {
	if s.params == nil {
		s.params = map[string]json.RawMessage{}
	}
	s.params[op], _ = json.Marshal(params)
	b, _ := json.Marshal(s.out[op])
	return json.Unmarshal(b, out)
}

func TestCheck(t *testing.T) {
	w := runtime.Workload{Key: "shop", Spec: runtime.Spec{}}
	for _, c := range []struct {
		ps      helper.DockerOutput
		status  string
		problem string
	}{
		{helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"running"}` + "\n" + `{"Service":"init","State":"exited","ExitCode":0}`}, runtime.Healthy, ""},
		{helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"running","Health":"starting"}`}, runtime.Unknown, "compose service web HEALTHCHECK starting"},
		{helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"running","Health":"unhealthy"}`}, runtime.Unhealthy, "compose service web HEALTHCHECK failed"},
		{helper.DockerOutput{OK: true, Output: `{"Service":"web","State":"exited","ExitCode":1}`}, runtime.Unhealthy, "compose service web is not running (exited, exit 1)"},
		{helper.DockerOutput{OK: true, Output: `{"Service":"init","State":"exited","ExitCode":0}`}, runtime.Unhealthy, "compose project shop has no running services"},
		{helper.DockerOutput{OK: true, Output: ""}, runtime.Missing, "compose project shop has no services"},
	} {
		got := Runtime{Helper: &scripted{out: map[string]helper.DockerOutput{"compose.ps": c.ps}}}.Check(context.Background(), w)
		if got.Status != c.status || got.Problem != c.problem {
			t.Errorf("%s: %+v, want %s %q", c.ps.Output, got, c.status, c.problem)
		}
	}
}

func TestApplyWithoutBindingRemovesAnOldOverride(t *testing.T) {
	root := t.TempDir()
	h := &scripted{out: map[string]helper.DockerOutput{
		"compose.up": {OK: true}, "compose.ps": {OK: true, Output: `{"Service":"web","State":"running"}`},
	}}
	art := filepath.Join(t.TempDir(), "a")
	_ = os.WriteFile(art, []byte("services: {}\n"), 0o600)
	dir := filepath.Join(root, "shop")
	_ = os.MkdirAll(dir, 0o755)
	_ = os.WriteFile(filepath.Join(dir, overrideFile), []byte("{}"), 0o600)
	r := Runtime{Helper: h, Dir: root}
	if _, err := r.Apply(context.Background(), runtime.Workload{Key: "shop", Spec: runtime.Spec{}, Artifact: art}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, overrideFile)); !os.IsNotExist(err) {
		t.Error("stale override kept")
	}
	if st, _ := os.Stat(dir); st.Mode().Perm() != 0o755 {
		t.Errorf("project dir mode %v", st.Mode())
	}
	var req helper.ComposeRequest
	_ = json.Unmarshal(h.params["compose.up"], &req)
	if req.EnvFile != "" || req.OverrideFile != "" || !strings.HasSuffix(req.File, "/shop/compose.yaml") {
		t.Fatalf("compose.up %+v", req)
	}
	h.out["compose.up"] = helper.DockerOutput{OK: false, Output: "yaml: line 3: bad"}
	if _, err := r.Apply(context.Background(), runtime.Workload{Key: "shop", Spec: runtime.Spec{}, Artifact: art}); err == nil ||
		err.Error() != "docker compose up shop failed: yaml: line 3: bad" {
		t.Fatalf("err = %v", err)
	}
	h.out["compose.down"] = helper.DockerOutput{OK: true}
	if err := r.Remove(context.Background(), runtime.Workload{Key: "shop", Spec: runtime.Spec{}}); err != nil {
		t.Fatal(err)
	}
}
