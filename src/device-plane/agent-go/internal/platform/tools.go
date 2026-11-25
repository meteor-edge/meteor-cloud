package platform

import (
	"context"
	"os"
	"time"
)

// composeTimeout bounds `docker compose version`; it starts the Docker CLI only.
const composeTimeout = 10 * time.Second

// Tools says which runtimes and tools are installed. The control plane skips a
// deployment whose runtime is reported missing.
type Tools struct {
	Systemd       bool
	Docker        bool
	DockerCompose bool
	WireGuard     bool
	SSH           bool
}

// Tools probes the installed tools the same way the Python agent did.
func (h *Host) Tools(ctx context.Context) Tools {
	t := Tools{
		Systemd:   h.has("systemctl") && (h.exists("/run/systemd/system") || h.exists("/run/systemd")),
		WireGuard: h.has("wg") && h.has("wg-quick"),
		SSH:       h.exists("/usr/sbin/sshd") || h.has("sshd"),
	}
	if docker := h.DockerBinary(); docker != "" {
		t.Docker = true
		ctx, cancel := context.WithTimeout(ctx, composeTimeout)
		defer cancel()
		t.DockerCompose = h.Run(ctx, docker, "compose", "version")
	}
	return t
}

// DockerBinary returns the docker CLI's path, or "" when Docker is not
// installed. A service PATH may leave out snap, so common places are checked too.
func (h *Host) DockerBinary() string {
	if p, err := h.LookPath("docker"); err == nil {
		return p
	}
	for _, p := range []string{"/usr/bin/docker", "/usr/local/bin/docker", "/snap/bin/docker"} {
		if st, err := os.Stat(h.path(p)); err == nil && st.Mode().IsRegular() && st.Mode()&0o111 != 0 {
			return h.path(p)
		}
	}
	return ""
}

func (h *Host) has(tool string) bool {
	_, err := h.LookPath(tool)
	return err == nil
}

func (h *Host) exists(p string) bool {
	_, err := os.Stat(h.path(p))
	return err == nil
}
