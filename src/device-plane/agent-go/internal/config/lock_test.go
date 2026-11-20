package config

import (
	"os"
	"testing"
	"time"
)

func TestLockIdentityIsExclusive(t *testing.T) {
	p := DefaultPaths(t.TempDir())
	unlock, err := LockIdentity(p) // no directory yet: nothing to lock
	if err != nil {
		t.Fatal(err)
	}
	unlock()
	if err := os.MkdirAll(p.IdentityDir, 0o700); err != nil {
		t.Fatal(err)
	}
	first, err := LockIdentity(p)
	if err != nil {
		t.Fatal(err)
	}
	got := make(chan struct{})
	go func() {
		second, err := LockIdentity(p)
		if err == nil {
			second()
		}
		close(got)
	}()
	select {
	case <-got:
		t.Fatal("a second holder got the lock while the first held it")
	case <-time.After(50 * time.Millisecond):
	}
	first()
	select {
	case <-got:
	case <-time.After(5 * time.Second):
		t.Fatal("the lock was not released")
	}
}
