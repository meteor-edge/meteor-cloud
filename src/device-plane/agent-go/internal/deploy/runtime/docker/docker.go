// Package docker is the deploy runtime for kind "docker": one container, from
// a pulled image (pinned to spec.image_digest when given) or from a loaded
// `docker save` tarball. The root helper runs the docker commands.
package docker

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/helper"
)

var (
	loadedImageRe = regexp.MustCompile(`(?i)Loaded image(?: ID)?:?\s*(.+)`)
	digestRe      = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)
)

// Runtime runs containers through the helper.
type Runtime struct {
	Helper runtime.Caller
}

// Kind implements runtime.Runtime.
func (Runtime) Kind() string { return "docker" }

func container(w runtime.Workload) (string, error) {
	return runtime.CheckName("container_name", w.Spec.Text("container_name", w.Key))
}

// Apply implements runtime.Runtime: load the tarball (if any), replace the
// container, and fail when it is not running right after.
func (r Runtime) Apply(ctx context.Context, w runtime.Workload) (runtime.Result, error) {
	name, err := container(w)
	if err != nil {
		return runtime.Result{}, err
	}
	image := strings.TrimSpace(w.Spec.Text("image", ""))
	if image != "" && w.Artifact == "" {
		if image, err = PinnedImage(image, w.Spec.Text("image_digest", "")); err != nil {
			return runtime.Result{}, err
		}
	}
	if w.Artifact != "" {
		loaded, err := r.load(ctx, w.Artifact)
		if err != nil {
			return runtime.Result{}, err
		}
		if image == "" {
			image = loaded
		}
	}
	if image == "" {
		return runtime.Result{}, runtime.Failf("A docker release needs an image name or a loadable tarball.")
	}
	command, err := Split(w.Spec.Text("command", ""))
	if err != nil {
		return runtime.Result{}, err
	}
	req := helper.DockerRunRequest{
		Name: name, Image: image, Ports: w.Spec.Strings("ports"), Command: command,
		Env:     runtime.BoundEnv(w.Spec.Env("env"), w.App),
		Restart: restart(w.Spec),
	}
	if helper.ValidLabel(w.Key) {
		req.Workload = w.Key
		if helper.ValidLabel(w.Release) {
			req.Release = w.Release
		}
	}
	if w.App != nil {
		req.TokenFile = w.App.TokenFile
	}
	// An image from a verified artifact runs as loaded: when the tarball does
	// not hold the named image, docker run fails instead of pulling a tag
	// that may have changed since the release was signed.
	req.PullNever = w.Artifact != ""
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "docker.run", req, &out); err != nil {
		return runtime.Result{}, err
	}
	if !out.OK {
		return runtime.Result{}, runtime.Failf("docker run %s failed: %s", name, detail(out.Output))
	}
	h := r.Check(ctx, w)
	if h.Status == runtime.Unhealthy || h.Status == runtime.Missing {
		return runtime.Result{}, runtime.Failf("%s after apply.", h.Problem)
	}
	return runtime.Result{Health: h}, nil
}

// restart is spec.restart, "unless-stopped" when missing, none when null or "".
func restart(s runtime.Spec) string {
	if _, ok := s["restart"]; !ok {
		return "unless-stopped"
	}
	return s.Text("restart", "")
}

// load runs docker load and returns the image it reported.
func (r Runtime) load(ctx context.Context, tarball string) (string, error) {
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "docker.load", helper.DockerPathRequest{Path: tarball}, &out); err != nil {
		return "", err
	}
	if !out.OK {
		return "", runtime.Failf("docker load failed: %s", detail(out.Output))
	}
	for _, line := range strings.Split(out.Output, "\n") {
		if m := loadedImageRe.FindStringSubmatch(strings.TrimSpace(line)); m != nil {
			return strings.TrimSpace(m[1]), nil
		}
	}
	return "", runtime.Failf("docker load succeeded but no image name was reported.")
}

// Check implements runtime.Runtime from one docker inspect: running, not
// restarting, and its HEALTHCHECK (if the image has one) not failing.
func (r Runtime) Check(ctx context.Context, w runtime.Workload) runtime.Health {
	name, err := container(w)
	if err != nil {
		return runtime.Health{Status: runtime.Unhealthy, Problem: err.Error()}
	}
	subject := "container " + name
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "docker.health", helper.DockerNameRequest{Name: name}, &out); err != nil {
		return runtime.Health{Status: runtime.Unhealthy, Problem: subject + " could not be inspected"}
	}
	if !out.OK {
		if gone(out.Output) {
			return runtime.Health{Status: runtime.Missing, Problem: subject + " not found"}
		}
		// The daemon could not answer: not a reason to replace the container.
		return runtime.Health{Status: runtime.Unhealthy, Problem: subject + " could not be inspected: " + detail(out.Output)}
	}
	parts := append(strings.Split(strings.TrimSpace(out.Output), "|"), "", "", "", "")
	running, status, exitCode, health := parts[0], parts[1], parts[2], parts[3]
	// Docker reports a crash-looping container as running with status "restarting".
	if !strings.EqualFold(running, "true") || status == "restarting" {
		var bits []string
		if status != "" {
			bits = append(bits, status)
		}
		if exitCode != "" {
			bits = append(bits, "exit "+exitCode)
		}
		problem := subject + " is not running"
		if len(bits) > 0 {
			problem += " (" + strings.Join(bits, ", ") + ")"
		}
		return runtime.Health{Status: runtime.Unhealthy, Problem: problem}
	}
	switch health {
	case "unhealthy":
		return runtime.Health{Status: runtime.Unhealthy, Problem: subject + " HEALTHCHECK failed"}
	case "starting":
		// Coming up: neither healthy nor failed yet.
		return runtime.Health{Status: runtime.Unknown, Problem: subject + " HEALTHCHECK starting"}
	}
	return runtime.Health{Status: runtime.Healthy}
}

// Remove implements runtime.Runtime.
func (r Runtime) Remove(ctx context.Context, w runtime.Workload) error {
	name, err := container(w)
	if err != nil {
		return err
	}
	var out helper.DockerOutput
	if err := r.Helper.Call(ctx, "docker.remove", helper.DockerNameRequest{Name: name}, &out); err != nil {
		return err
	}
	if !out.OK {
		return runtime.Failf("docker rm %s failed: %s", name, detail(out.Output))
	}
	return nil
}

// PinnedImage turns repo:tag plus a release's image_digest into repo@sha256:...,
// so Docker checks the content it pulls.
func PinnedImage(image, digest string) (string, error) {
	if digest == "" {
		return image, nil
	}
	if !digestRe.MatchString(digest) {
		return "", runtime.Failf("Invalid image digest %s.", pyQuote(digest))
	}
	name, _, _ := strings.Cut(image, "@")
	last := name[strings.LastIndex(name, "/")+1:]
	if i := strings.Index(last, ":"); i >= 0 {
		name = name[:len(name)-len(last)+i]
	}
	return name + "@" + digest, nil
}

func detail(output string) string {
	if s := strings.TrimSpace(output); s != "" {
		if len(s) > 400 {
			s = s[:400]
		}
		return s
	}
	return "no output"
}

// pyQuote writes s as Python's repr of a str does for plain text.
func pyQuote(s string) string { return fmt.Sprintf("'%s'", strings.ReplaceAll(s, "'", `\'`)) }

// gone reports whether docker inspect failed because the container does not exist.
func gone(output string) bool {
	o := strings.ToLower(output)
	return strings.Contains(o, "no such object") || strings.Contains(o, "no such container")
}
