package helper

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

var pinned = "registry.example.com:5000/acme/team/web@sha256:" + strings.Repeat("a", 64)

func TestDockerPullKeepsTheCredentialInAPrivateFile(t *testing.T) {
	f := &fakeHost{output: "ok\n"}
	h, _ := deployHost(t, f)
	var dir string
	run := h.Run
	h.Run = func(ctx context.Context, name string, args ...string) ([]byte, error) {
		dir = args[slices.Index(args, "--config")+1]
		for path, mode := range map[string]os.FileMode{dir: 0o700, filepath.Join(dir, "config.json"): 0o600} {
			if st, err := os.Stat(path); err != nil || st.Mode().Perm() != mode {
				t.Errorf("%s: %v, %v", path, st, err)
			}
		}
		data, _ := os.ReadFile(filepath.Join(dir, "config.json"))
		// base64("device:s3cr3t")
		if want := `{"auths":{"registry.example.com:5000":{"auth":"ZGV2aWNlOnMzY3IzdA=="}}}`; string(data) != want {
			t.Errorf("config %s", data)
		}
		if strings.Contains(strings.Join(args, " "), "s3cr3t") || !slices.Equal(args[2:], []string{"pull", "--quiet", pinned}) {
			t.Errorf("args %q", args)
		}
		return run(ctx, name, args...)
	}
	req := DockerPullRequest{Image: pinned, Registry: "registry.example.com:5000", Username: "device", Password: "s3cr3t"}
	if _, err := call(t, h, "docker.pull", req); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Errorf("the credential stays behind: %v", err)
	}
	raw, _ := json.Marshal(req)
	if fields := auditDockerPull(raw); len(fields) != 2 || fields[1] != pinned {
		t.Errorf("audit %v", fields)
	}
}

func TestDockerPullRejects(t *testing.T) {
	ok := DockerPullRequest{Image: pinned, Registry: "registry.example.com:5000", Username: "u", Password: "p"}
	for name, change := range map[string]func(*DockerPullRequest){
		"tag":            func(r *DockerPullRequest) { r.Image = "registry.example.com:5000/acme/web:1" },
		"other registry": func(r *DockerPullRequest) { r.Registry = "evil.example.com" },
		"option":         func(r *DockerPullRequest) { r.Image = "--all-tags" },
		"no password":    func(r *DockerPullRequest) { r.Password = "" },
		"colon in user":  func(r *DockerPullRequest) { r.Username = "a:b" },
	} {
		f := &fakeHost{}
		h, _ := deployHost(t, f)
		r := ok
		change(&r)
		if _, err := call(t, h, "docker.pull", r); err == nil {
			t.Errorf("%s accepted", name)
		}
	}
}

func TestEngineEndpointAndCLI(t *testing.T) {
	h := Host{}
	if cli, args := h.engineArgs("ps"); cli != "docker" || !slices.Equal(args, []string{"ps"}) {
		t.Errorf("default %s %q", cli, args)
	}
	h.Engine = Engine{Name: "other", CLI: "other", Endpoint: "unix:///run/other.sock"}
	if cli, args := h.engineArgs("ps"); cli != "other" || !slices.Equal(args, []string{"--host", "unix:///run/other.sock", "ps"}) {
		t.Errorf("configured %s %q", cli, args)
	}
}
