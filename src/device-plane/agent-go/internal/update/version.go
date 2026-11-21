package update

import (
	"fmt"
	"strconv"
	"strings"
)

// version is a parsed semantic version (X.Y.Z[-pre][+build]). Build metadata
// is dropped: two builds of one version are the same version.
type version struct {
	core [3]uint64
	pre  []string
}

// parseVersion reads a semantic version, with or without a leading "v" or
// "meteorcli ". Edge builds are versioned 0.0.0-edge.<run>+<commit>, so their
// run number orders them.
func parseVersion(s string) (version, error) {
	s = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(s), "meteorcli "))
	s = strings.TrimPrefix(s, "v")
	if i := strings.IndexByte(s, '+'); i >= 0 {
		s = s[:i]
	}
	var v version
	core := s
	if i := strings.IndexByte(s, '-'); i >= 0 {
		core = s[:i]
		v.pre = strings.Split(s[i+1:], ".")
		for _, p := range v.pre {
			if p == "" {
				return version{}, fmt.Errorf("version %q: empty pre-release part", s)
			}
		}
	}
	parts := strings.Split(core, ".")
	if len(parts) != 3 {
		return version{}, fmt.Errorf("version %q is not X.Y.Z", s)
	}
	for i, p := range parts {
		n, err := strconv.ParseUint(p, 10, 64)
		if err != nil || p == "" {
			return version{}, fmt.Errorf("version %q is not X.Y.Z", s)
		}
		v.core[i] = n
	}
	return v, nil
}

// compare returns -1, 0 or 1 like semver precedence: the numbers first, then
// a release beats any of its pre-releases, then the pre-release parts (numbers
// compare as numbers and sort before words).
func (a version) compare(b version) int {
	for i := range a.core {
		if a.core[i] != b.core[i] {
			return cmp(a.core[i] < b.core[i])
		}
	}
	switch {
	case len(a.pre) == 0 && len(b.pre) == 0:
		return 0
	case len(a.pre) == 0:
		return 1
	case len(b.pre) == 0:
		return -1
	}
	for i := 0; i < len(a.pre) && i < len(b.pre); i++ {
		x, y := a.pre[i], b.pre[i]
		if x == y {
			continue
		}
		nx, ex := strconv.ParseUint(x, 10, 64)
		ny, ey := strconv.ParseUint(y, 10, 64)
		switch {
		case ex == nil && ey == nil:
			return cmp(nx < ny)
		case ex == nil:
			return -1
		case ey == nil:
			return 1
		default:
			return cmp(x < y)
		}
	}
	if len(a.pre) == len(b.pre) {
		return 0
	}
	return cmp(len(a.pre) < len(b.pre))
}

func cmp(less bool) int {
	if less {
		return -1
	}
	return 1
}

// Newer reports whether candidate is a higher version than current. Like the
// Python agent, a current version that does not parse (a hand-made build) is
// treated as older than any release; a candidate that does not parse is never
// newer.
func Newer(candidate, current string) bool {
	c, err := parseVersion(candidate)
	if err != nil {
		return false
	}
	cur, err := parseVersion(current)
	if err != nil {
		return true
	}
	return c.compare(cur) > 0
}

// Same reports whether two version strings are the same version (build
// metadata ignored). Strings that do not parse are the same only when equal.
func Same(a, b string) bool {
	va, err1 := parseVersion(a)
	vb, err2 := parseVersion(b)
	if err1 != nil || err2 != nil {
		return strings.TrimSpace(a) == strings.TrimSpace(b)
	}
	return va.compare(vb) == 0
}
