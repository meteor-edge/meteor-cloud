package compose

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

func TestRegistryImagesArePulledAndTaggedFirst(t *testing.T) {
	d := "sha256:" + strings.Repeat("ab", 32)
	art := filepath.Join(t.TempDir(), "a")
	_ = os.WriteFile(art, []byte("services: {}\n"), 0o600)
	w := runtime.Workload{
		Key: "shop", Artifact: art,
		Spec: runtime.Spec{"registry_images": []any{"localhost:5000/acme/web:1.0@" + d}},
		PullAuth: func(context.Context) (runtime.PullAuth, error) {
			return runtime.PullAuth{Registry: "localhost:5000", Username: "device", Password: "pw"}, nil
		},
	}
	h := &scripted{out: map[string]helper.DockerOutput{
		"docker.pull": {OK: true}, "docker.tag": {OK: true},
		"compose.up": {OK: true}, "compose.ps": {OK: true, Output: `{"Service":"web","State":"running"}`},
	}}
	if _, err := (Runtime{Helper: h, Dir: t.TempDir()}).Apply(context.Background(), w); err != nil {
		t.Fatal(err)
	}
	var tag helper.DockerTagRequest
	_ = json.Unmarshal(h.params["docker.tag"], &tag)
	if tag.Image != "localhost:5000/acme/web@"+d || tag.Tag != "localhost:5000/acme/web:1.0" {
		t.Errorf("tag %+v", tag)
	}
	h = &scripted{out: map[string]helper.DockerOutput{"docker.pull": {Output: "denied"}}}
	if _, err := (Runtime{Helper: h, Dir: t.TempDir()}).Apply(context.Background(), w); err == nil || h.params["compose.up"] != nil {
		t.Errorf("err %v, up %s", err, h.params["compose.up"])
	}
	w.Spec = runtime.Spec{"registry_images": []any{"localhost:5000/acme/web@" + d}}
	if _, err := (Runtime{Helper: h, Dir: t.TempDir()}).Apply(context.Background(), w); err == nil {
		t.Error("an image without a tag was accepted")
	}
}
