// Package network holds the modules that manage how the device is reached:
// wifi (Wi-Fi changes ordered from the console, kept only when MeteorCloud
// answers on the new network) and wireguard (the device tunnel and the SSH
// keys the console's browser terminal uses). They do the privileged part
// through the root helper (wifi.*, wireguard.*, ssh.authorized_keys) and never
// call each other.
package network

import (
	"context"
	"errors"
	"time"

	"meteorcloud/meteorcli/internal/api"
)

// caller is the part of the helper client the modules use.
type caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// noHelper fails every call (an agent started without a helper socket).
type noHelper struct{}

func (noHelper) Call(context.Context, string, any, any) error {
	return errors.New("the root helper is not available")
}

// codeOf returns the error code the server sent ("" when there is none).
func codeOf(err error) string {
	var e *api.Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

// sleep waits d or until ctx ends; it returns false when ctx ended.
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// cut shortens s to n bytes without splitting a UTF-8 character.
func cut(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && s[n]&0xc0 == 0x80 {
		n--
	}
	return s[:n]
}
