package deploy

import (
	"encoding/json"
	"strconv"

	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// item is one desired-software entry as decoded JSON. It stays a map because
// the signature covers every field, including ones this agent does not know,
// and the applied state keeps it whole for a later rollback.
type item map[string]any

// str returns a string field, "" when missing or not a string.
func (it item) str(k string) string {
	s, _ := it[k].(string)
	return s
}

func (it item) key() string            { return it.str("workload_key") }
func (it item) kind() string           { return it.str("kind") }
func (it item) version() string        { return it.str("release_version") }
func (it item) artifactID() string     { return it.str("artifact_id") }
func (it item) artifactSHA256() string { return it.str("artifact_sha256") }

func (it item) spec() runtime.Spec {
	m, _ := it["spec"].(map[string]any)
	return runtime.Spec(m)
}

// identity is the release and revision; ok is false without a release id.
func (it item) identity() (release string, revision int, ok bool) {
	switch v := it["release_id"].(type) {
	case string:
		release = v
	case nil:
	default:
		release = runtime.Stringify(v)
	}
	if release == "" {
		return "", 0, false
	}
	return release, toInt(it["revision"]), true
}

// toInt reads a JSON number (as json.Number, float64 from a state file, or
// text), 0 when it is none.
func toInt(v any) int {
	switch x := v.(type) {
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return int(n)
		}
		if f, err := x.Float64(); err == nil {
			return int(f)
		}
	case float64:
		return int(x)
	case int:
		return x
	case string:
		if n, err := strconv.Atoi(x); err == nil {
			return n
		}
	}
	return 0
}

// pyRepr writes a kind the way Python's repr() shows it in the Unsupported message.
func pyRepr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case string:
		return "'" + x + "'"
	}
	return runtime.Stringify(v)
}
