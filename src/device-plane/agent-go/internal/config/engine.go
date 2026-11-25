package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
)

// Engine is the container engine of this device (/etc/meteorcli/engine.json).
// Any engine with the Docker API works; the docker CLI talks to its socket.
type Engine struct {
	// Name is the engine's id: "docker", "podman", ...
	Name string `json:"name"`
	// Endpoint is its API socket, "unix:///run/podman/podman.sock".
	Endpoint string `json:"endpoint"`
}

const podmanEndpoint = "unix:///run/podman/podman.sock"

var engineNameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,31}$`)

// EngineFile is where an administrator chooses the engine.
func (p Paths) EngineFile() string { return filepath.Join(filepath.Dir(p.ConfigFile), "engine.json") }

// Socket is the endpoint's path, "" for Docker's default.
func (e Engine) Socket() string { return strings.TrimPrefix(e.Endpoint, "unix://") }

// LoadEngine reads the engine file; Docker when there is none. The root helper
// obeys it, so only root may be able to write it.
func LoadEngine(path string) (Engine, error) {
	docker := Engine{Name: "docker"}
	f, err := os.Open(path)
	if errors.Is(err, fs.ErrNotExist) {
		return docker, nil
	}
	if err != nil {
		return docker, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return docker, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !st.Mode().IsRegular() || st.Mode().Perm()&0o022 != 0 || !ok || (sys.Uid != 0 && int(sys.Uid) != os.Geteuid()) {
		return docker, fmt.Errorf("%s must belong to root and be writable only by root", path)
	}
	var e Engine
	if err := json.NewDecoder(io.LimitReader(f, 1<<16)).Decode(&e); err != nil {
		return docker, fmt.Errorf("%s: %w", path, err)
	}
	if e.Name == "podman" && e.Endpoint == "" {
		e.Endpoint = podmanEndpoint
	}
	if !engineNameRe.MatchString(e.Name) {
		return docker, fmt.Errorf("%s: %q is not an engine name", path, e.Name)
	}
	if e.Endpoint != "" && (!strings.HasPrefix(e.Endpoint, "unix:///") || filepath.Clean(e.Socket()) != e.Socket()) {
		return docker, fmt.Errorf("%s: endpoint must be unix:///path/to.sock", path)
	}
	if e.Name != "docker" && e.Endpoint == "" {
		return docker, fmt.Errorf("%s: %s needs an endpoint", path, e.Name)
	}
	return e, nil
}
