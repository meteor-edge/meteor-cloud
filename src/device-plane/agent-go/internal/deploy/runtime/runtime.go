// Package runtime defines what a deploy kind (systemd, docker, docker_compose)
// must do, and the small types the runtimes and the deploy module share. Each
// kind lives in its own package below this one and does its privileged work
// through the root helper; the deploy module finds them in deploy/runtimes.go.
package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"strings"
)

// Caller is the part of the helper client a runtime uses.
type Caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// Health states. Missing means the workload is gone (a `docker rm`): the
// deploy module installs it again instead of reporting it unhealthy.
const (
	Healthy   = "healthy"
	Unhealthy = "unhealthy"
	Unknown   = "unknown"
	Missing   = "missing"
)

// Health is what a runtime says about a workload right now. Problem says why it
// is not healthy and is shown in the console.
type Health struct {
	Status  string
	Problem string
}

// TokenMountPath is where a bound container finds its application token.
const TokenMountPath = "/run/secrets/meteorcloud-token"

// App is the application identity of a workload bound to a MeteorCloud
// application: a token file mounted read-only into the container, and the
// METEORCLOUD_* variables the SDK reads. The token itself is never in Env.
type App struct {
	TokenFile string
	Env       map[string]string
}

// Workload is one desired item, ready to apply.
type Workload struct {
	// Key is the workload_key; names default to it.
	Key string
	// Kind is the deploy kind.
	Kind string
	// Spec is the release's kind-specific settings.
	Spec Spec
	// Artifact is the downloaded artifact's path, "" when the release has none.
	Artifact string
	// App is set when the release is bound to an application.
	App *App
	// ForceRecreate creates containers again although nothing changed (a
	// mounted token file was replaced).
	ForceRecreate bool
}

// Result is what Apply achieved.
type Result struct {
	// Health right after the apply: Healthy, or Unknown while a HEALTHCHECK starts.
	Health Health
}

// Runtime installs and watches workloads of one kind. Apply returns an error
// whose text is shown in the console when the install failed, including a
// workload that is not up right after it.
type Runtime interface {
	Kind() string
	Apply(ctx context.Context, w Workload) (Result, error)
	Check(ctx context.Context, w Workload) Health
	Remove(ctx context.Context, w Workload) error
}

// Spec is a release spec as decoded JSON. Its getters follow the Python
// agent's `spec.get(key) or default` rules.
type Spec map[string]any

// Text returns the value of key as text, or def when it is missing or false
// (null, "", 0, ...).
func (s Spec) Text(key, def string) string {
	if s.Bool(key, false) {
		return Stringify(s[key])
	}
	return def
}

// Bool returns key's truth value (Python's bool()), or def when key is missing.
func (s Spec) Bool(key string, def bool) bool {
	v, ok := s[key]
	if !ok {
		return def
	}
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		f, err := x.Float64()
		return err != nil || f != 0
	case float64: // a spec read back from a state file
		return x != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// Strings returns key as a list of strings (each element as text).
func (s Spec) Strings(key string) []string {
	list, _ := s[key].([]any)
	out := make([]string, 0, len(list))
	for _, e := range list {
		out = append(out, Stringify(e))
	}
	return out
}

// Env returns key as a map of strings (values as Python's str() would write them).
func (s Spec) Env(key string) map[string]string {
	m, _ := s[key].(map[string]any)
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = Stringify(v)
	}
	return out
}

// Stringify writes a JSON value as Python's str() does for the simple types.
func Stringify(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	case json.Number:
		return x.String()
	case float64:
		return fmt.Sprint(x)
	}
	data, _ := json.Marshal(v)
	return string(data)
}

// BoundEnv is the release's env plus the app's METEORCLOUD_* variables; a
// release may set its own METEORCLOUD_ENDPOINT.
func BoundEnv(env map[string]string, app *App) map[string]string {
	out := maps.Clone(env)
	if out == nil {
		out = map[string]string{}
	}
	if app == nil {
		return out
	}
	endpoint := out["METEORCLOUD_ENDPOINT"]
	maps.Copy(out, app.Env)
	if endpoint != "" {
		out["METEORCLOUD_ENDPOINT"] = endpoint
	}
	return out
}

// Failure is an error whose text is meant for the console (a software report's
// last_error), worded like the Python agent's messages.
type Failure string

func (f Failure) Error() string { return string(f) }

// Failf returns a Failure.
func Failf(format string, args ...any) error { return Failure(fmt.Sprintf(format, args...)) }

// CheckName refuses a blank name, a path or a hidden name, as the Python agent did.
func CheckName(what, name string) (string, error) {
	name = strings.TrimSpace(name)
	switch {
	case name == "":
		return "", Failf("%s must not be blank", what)
	case strings.Contains(name, "/") || strings.HasPrefix(name, "."):
		return "", Failf("Invalid %s: %s", what, name)
	}
	return name, nil
}
