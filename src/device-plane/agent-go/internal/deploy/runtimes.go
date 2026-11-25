package deploy

import (
	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/deploy/runtime"
	"meteorcloud/meteorcli/internal/deploy/runtime/compose"
	"meteorcloud/meteorcli/internal/deploy/runtime/docker"
	"meteorcloud/meteorcli/internal/deploy/runtime/systemd"
	"meteorcloud/meteorcli/internal/platform"
)

// kind is how one deploy kind is set up.
type kind struct {
	// make builds the runtime.
	make func(h runtime.Caller, p config.Paths) runtime.Runtime
	// installed says whether the device has the tool the kind needs.
	installed func(t platform.Tools) bool
	// missing is the report when it does not (the Python agent's wording).
	missing string
}

// kinds lists every deploy kind. Add a kind: a package under runtime/ and one
// line here.
var kinds = map[string]kind{
	"systemd": {
		make:      func(h runtime.Caller, _ config.Paths) runtime.Runtime { return systemd.Runtime{Helper: h} },
		installed: func(t platform.Tools) bool { return t.Systemd },
		missing:   "This device does not support systemd.",
	},
	"docker": {
		make:      func(h runtime.Caller, _ config.Paths) runtime.Runtime { return docker.Runtime{Helper: h} },
		installed: func(t platform.Tools) bool { return t.Docker },
		missing:   "This device does not support Docker.",
	},
	"docker_compose": {
		make: func(h runtime.Caller, p config.Paths) runtime.Runtime {
			return compose.Runtime{Helper: h, Dir: p.ComposeDir()}
		},
		installed: func(t platform.Tools) bool { return t.DockerCompose },
		missing:   "This device does not support Docker Compose.",
	},
}

// order is the order of Kinds (the Python agent's SUPPORTED_KINDS).
var order = []string{"systemd", "docker", "docker_compose"}

// Kinds returns the deploy kinds this agent can apply, for capabilities.deploy_kinds.
func Kinds() []string { return append([]string(nil), order...) }

// features are the optional release settings this agent applies. The server
// does not deploy a release that needs one the agent does not list.
var features = []string{"registry", "run_options", "compose_registry"}

// Features returns them, for capabilities.deploy_features.
func Features() []string { return append([]string(nil), features...) }
