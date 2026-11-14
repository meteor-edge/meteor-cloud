// Package streams validates stream names and matches them against permission patterns.
//
// A stream name is dot separated ("energy.power"). A pattern may use "*" for exactly
// one segment and a final "**" for one or more segments. The control plane validates
// patterns with the same rules (contracts/data-plane-v1.md).
package streams

import (
	"errors"
	"fmt"
	"strings"
)

const (
	MaxLength      = 128
	MaxSegments    = 8
	ReservedPrefix = "meteor"
)

var ErrInvalid = errors.New("invalid stream")

func validSegment(s string) bool {
	if len(s) == 0 || len(s) > 64 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= 'a' && c <= 'z', c >= '0' && c <= '9':
		case (c == '_' || c == '-') && i > 0:
		default:
			return false
		}
	}
	return true
}

// ValidateName checks a concrete stream name (no wildcards).
func ValidateName(name string) error {
	if name == "" || len(name) > MaxLength {
		return fmt.Errorf("%w: name must be 1-%d characters", ErrInvalid, MaxLength)
	}
	parts := strings.Split(name, ".")
	if len(parts) > MaxSegments {
		return fmt.Errorf("%w: at most %d segments", ErrInvalid, MaxSegments)
	}
	for _, p := range parts {
		if !validSegment(p) {
			return fmt.Errorf("%w: segments use a-z, 0-9, '_' or '-' and start with a letter or digit", ErrInvalid)
		}
	}
	return nil
}

// IsReserved reports whether applications may not publish to the stream.
func IsReserved(name string) bool {
	return name == ReservedPrefix || strings.HasPrefix(name, ReservedPrefix+".")
}

// ValidatePattern checks a subscription or permission pattern.
func ValidatePattern(pattern string) error {
	if pattern == "" || len(pattern) > MaxLength {
		return fmt.Errorf("%w: pattern must be 1-%d characters", ErrInvalid, MaxLength)
	}
	parts := strings.Split(pattern, ".")
	if len(parts) > MaxSegments {
		return fmt.Errorf("%w: at most %d segments", ErrInvalid, MaxSegments)
	}
	if pattern == "**" {
		return fmt.Errorf("%w: a bare '**' is not allowed", ErrInvalid)
	}
	for i, p := range parts {
		switch {
		case p == "*":
		case p == "**":
			if i != len(parts)-1 {
				return fmt.Errorf("%w: '**' may only end a pattern", ErrInvalid)
			}
		case !validSegment(p):
			return fmt.Errorf("%w: bad segment %q", ErrInvalid, p)
		}
	}
	return nil
}

// Match reports whether a concrete stream name matches the pattern.
func Match(pattern, name string) bool {
	pp := strings.Split(pattern, ".")
	np := strings.Split(name, ".")
	for i, p := range pp {
		if p == "**" {
			return len(np) > i
		}
		if i >= len(np) {
			return false
		}
		if p != "*" && p != np[i] {
			return false
		}
	}
	return len(pp) == len(np)
}

// MatchAny reports whether name matches at least one pattern.
func MatchAny(patterns []string, name string) bool {
	for _, p := range patterns {
		if Match(p, name) {
			return true
		}
	}
	return false
}

// Covers reports whether every stream matched by sub is also matched by grant, so a
// subscription pattern can be checked against a permission once, when it is created.
func Covers(grant, sub string) bool {
	gp := strings.Split(grant, ".")
	sp := strings.Split(sub, ".")
	for i, g := range gp {
		if g == "**" {
			// sub must have at least one more segment (of any kind) from here on.
			return len(sp) > i
		}
		if i >= len(sp) {
			return false
		}
		s := sp[i]
		if s == "**" {
			// sub matches arbitrarily deep names; only a grant "**" (handled above) covers that.
			return false
		}
		if g == "*" {
			continue
		}
		if s == "*" || s != g {
			return false
		}
	}
	return len(gp) == len(sp)
}

// CoveredByAny reports whether one of the grants covers the subscription pattern.
func CoveredByAny(grants []string, sub string) bool {
	for _, g := range grants {
		if Covers(g, sub) {
			return true
		}
	}
	return false
}
