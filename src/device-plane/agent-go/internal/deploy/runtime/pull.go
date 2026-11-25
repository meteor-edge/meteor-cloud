package runtime

import (
	"context"
	"strings"

	"meteorcloud/meteorcli/internal/helper"
)

// Pull brings an image from MeteorCloud's registry to the device, unless it is
// there already (a rollback or a lost container works offline).
func Pull(ctx context.Context, h Caller, image string, auth func(context.Context) (PullAuth, error)) error {
	if !strings.Contains(image, "@sha256:") {
		return Failf("An image in the MeteorCloud registry needs a digest; save the release again.")
	}
	var out helper.DockerOutput
	if err := h.Call(ctx, "docker.has_image", helper.DockerImageRequest{Image: image}, &out); err != nil {
		return err
	}
	if out.OK {
		return nil
	}
	a, err := auth(ctx)
	if err != nil {
		return err
	}
	out = helper.DockerOutput{}
	req := helper.DockerPullRequest{Image: image, Registry: a.Registry, Username: a.Username, Password: a.Password}
	if err := h.Call(ctx, "docker.pull", req, &out); err != nil {
		return err
	}
	if !out.OK {
		return Failf("docker pull %s failed: %s", image, Detail(out.Output))
	}
	return nil
}

// Detail is a tool's output, cut for a report.
func Detail(output string) string {
	if s := strings.TrimSpace(output); s != "" {
		if len(s) > 400 {
			s = s[:400]
		}
		return s
	}
	return "no output"
}
