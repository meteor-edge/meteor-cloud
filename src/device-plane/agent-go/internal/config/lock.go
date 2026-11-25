package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"syscall"
)

// LockIdentity takes an exclusive flock on the identity directory and returns
// the unlock function. `meteorcli config` (root) holds it while it drops one
// server's enrollment and saves the next server; the agent holds it while it
// stores a new identity and checks, under the lock, that config.json still
// names the server that issued it. So an approval collected from the old
// server can never be stored next to the new server's URL.
//
// A missing directory needs no lock (there is nothing to drop) and returns a
// no-op unlock.
func LockIdentity(p Paths) (unlock func(), err error) {
	f, err := os.Open(p.IdentityDir)
	if errors.Is(err, fs.ErrNotExist) {
		return func() {}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("lock identity: %w", err)
	}
	for {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
		if !errors.Is(err, syscall.EINTR) {
			break
		}
	}
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("lock identity: %w", err)
	}
	return func() {
		_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
		f.Close()
	}, nil
}
