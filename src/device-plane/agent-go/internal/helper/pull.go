package helper

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"meteorcloud/meteorcli/internal/localrpc"
)

var (
	// pinnedRe is the only kind of image docker.pull and docker.has_image take:
	// a repository on a named registry, by digest.
	pinnedRe   = regexp.MustCompile(`^([a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?)/([a-z0-9][a-z0-9._/-]*)@sha256:[a-f0-9]{64}$`)
	registryRe = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?$`)
)

// DockerImageRequest is the params of docker.has_image.
type DockerImageRequest struct {
	Image string `json:"image"`
}

// DockerPullRequest is the params of docker.pull: Image by digest, from
// Registry. Password is a secret: it goes in a private config file for this
// one command, never on a command line and never into the operator's
// /etc/meteorcli/docker.
type DockerPullRequest struct {
	Image    string `json:"image"`
	Registry string `json:"registry"`
	Username string `json:"username"`
	Password string `json:"password"`
}

func (r *DockerPullRequest) validate() error {
	m := pinnedRe.FindStringSubmatch(r.Image)
	switch {
	case m == nil || len(r.Image) > 512:
		return badRequest("%q is not an image with a registry and a digest", r.Image)
	case !registryRe.MatchString(r.Registry) || m[1] != r.Registry:
		// The credential must not go to another registry.
		return badRequest("image %q is not on registry %q", r.Image, r.Registry)
	case r.Username == "" || r.Password == "" || strings.Contains(r.Username, ":"):
		return badRequest("docker.pull needs a user name and a password")
	}
	return nil
}

// auditDockerPull logs which image, never the credential.
func auditDockerPull(raw json.RawMessage) []any {
	var r DockerPullRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"image", r.Image}
}

// dockerHasImage reports (OK) whether the image is on the device.
func (h Host) dockerHasImage(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p DockerImageRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if !pinnedRe.MatchString(p.Image) {
		return nil, badRequest("%q is not an image with a registry and a digest", p.Image)
	}
	return h.dockerRun(ctx, "image", "inspect", "-f", "{{.Id}}", p.Image)
}

func (h Host) dockerPull(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r DockerPullRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := r.validate(); err != nil {
		return nil, err
	}
	dir, err := os.MkdirTemp(h.TempDir, "meteorcli-pull-*") // 0700
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	auth := base64.StdEncoding.EncodeToString([]byte(r.Username + ":" + r.Password))
	config, err := json.Marshal(map[string]any{"auths": map[string]any{r.Registry: map[string]string{"auth": auth}}})
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), config, 0o600); err != nil {
		return nil, err
	}
	return h.dockerRun(ctx, "--config", dir, "pull", "--quiet", r.Image)
}
