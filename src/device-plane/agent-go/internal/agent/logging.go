package agent

import (
	"context"
	"io"
	"log/slog"
	"regexp"
	"strings"
)

// redacted replaces every secret in logs.
const redacted = "[redacted]"

// sensitiveKey matches attribute names whose values are secrets.
var sensitiveKey = regexp.MustCompile(`(?i)(token|password|passwd|psk|secret|authorization|api[_-]?key|private[_-]?key|credential)`)

// sensitiveText finds secrets inside free text: "Bearer x", "password=x",
// `"device_token": "x"`, and the token prefixes the control plane issues
// (src/control-plane/app/devices/tokens.py, mqtt/credentials.py, applications).
var sensitiveText = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(bearer\s+)[^\s"',;]+`),
	// key=value, key: 'value', "key": "value with spaces and \"escapes\"".
	regexp.MustCompile(`(?i)((?:token|password|passwd|psk|secret|api[_-]?key|authorization)['"]?\s*[:=]\s*)("(?:[^"\\]|\\.)*"|'[^']*'|[^\s"',;&}]+)`),
	regexp.MustCompile(`\b(?:reg|dev|key|clm|sh|mqtt|mca)_[A-Za-z0-9_\-]{20,}`),
}

// Redact removes tokens, passwords, PSKs and Authorization values from s.
func Redact(s string) string {
	for i, re := range sensitiveText {
		if i == len(sensitiveText)-1 {
			s = re.ReplaceAllString(s, redacted)
			continue
		}
		s = re.ReplaceAllString(s, "${1}"+redacted)
	}
	return s
}

// NewLogger returns the agent's logger: text lines (journald adds the time) with
// every secret redacted, whatever the caller passes.
func NewLogger(w io.Writer, level slog.Leveler) *slog.Logger {
	inner := slog.NewTextHandler(w, &slog.HandlerOptions{
		Level: level,
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if len(groups) == 0 && a.Key == slog.TimeKey {
				return slog.Attr{} // journald stamps lines itself
			}
			return a
		},
	})
	return slog.New(redactHandler{inner: inner})
}

// redactHandler wraps a handler and redacts the message and every attribute.
// secretGroup is set once WithGroup opened a group with a sensitive name: every
// value logged inside it is then a secret, as with an explicit slog.Group.
type redactHandler struct {
	inner       slog.Handler
	secretGroup bool
}

func (h redactHandler) Enabled(ctx context.Context, l slog.Level) bool {
	return h.inner.Enabled(ctx, l)
}

func (h redactHandler) Handle(ctx context.Context, r slog.Record) error {
	out := slog.NewRecord(r.Time, r.Level, Redact(r.Message), r.PC)
	r.Attrs(func(a slog.Attr) bool {
		out.AddAttrs(redactAttr(a, h.secretGroup))
		return true
	})
	return h.inner.Handle(ctx, out)
}

func (h redactHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	clean := make([]slog.Attr, len(attrs))
	for i, a := range attrs {
		clean[i] = redactAttr(a, h.secretGroup)
	}
	return redactHandler{inner: h.inner.WithAttrs(clean), secretGroup: h.secretGroup}
}

func (h redactHandler) WithGroup(name string) slog.Handler {
	return redactHandler{inner: h.inner.WithGroup(name), secretGroup: h.secretGroup || isSensitiveKey(name)}
}

func isSensitiveKey(key string) bool {
	return sensitiveKey.MatchString(key) && !strings.HasSuffix(strings.ToLower(key), "_present")
}

// redactAttr redacts a; inSecret means a sits inside a group with a sensitive name.
func redactAttr(a slog.Attr, inSecret bool) slog.Attr {
	v := a.Value.Resolve()
	if (inSecret || isSensitiveKey(a.Key)) && v.Kind() != slog.KindGroup {
		return slog.String(a.Key, redacted)
	}
	secret := inSecret || isSensitiveKey(a.Key)
	switch v.Kind() {
	case slog.KindGroup:
		attrs := v.Group()
		clean := make([]any, len(attrs))
		for i, g := range attrs {
			clean[i] = redactAttr(g, secret)
		}
		return slog.Group(a.Key, clean...)
	case slog.KindString:
		return slog.String(a.Key, Redact(v.String()))
	case slog.KindAny:
		if err, ok := v.Any().(error); ok {
			return slog.String(a.Key, Redact(err.Error()))
		}
		return slog.String(a.Key, Redact(v.String()))
	default:
		return slog.Attr{Key: a.Key, Value: v}
	}
}
