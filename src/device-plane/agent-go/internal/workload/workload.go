// Package workload is what the deploy module and the workload actions share:
// which workloads the user stopped on purpose, and the lock that keeps an
// action and a deploy of the same device from running at once.
package workload

import (
	"context"
	"errors"
	"io/fs"
	"slices"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/state"
)

// stopsFile is the state file of the workloads the user stopped.
const stopsFile = "workload-stops.json"

const stopsVersion = 1

// Stop says what of a workload the user stopped: all of it, or some services.
type Stop struct {
	All      bool     `json:"all,omitempty"`
	Services []string `json:"services,omitempty"`
}

// Any reports whether anything is stopped.
func (s Stop) Any() bool { return s.All || len(s.Services) > 0 }

// With returns s with service ("" for all) stopped.
func (s Stop) With(service string) Stop {
	if service == "" || s.All {
		return Stop{All: true}
	}
	if slices.Contains(s.Services, service) {
		return s
	}
	return Stop{Services: append(slices.Clone(s.Services), service)}
}

// Without returns s with service ("" for all) started again.
func (s Stop) Without(service string) Stop {
	if service == "" {
		return Stop{}
	}
	return Stop{All: s.All, Services: slices.DeleteFunc(slices.Clone(s.Services), func(x string) bool { return x == service })}
}

// stopsMu guards the read-modify-write of stopsFile in this process.
var stopsMu sync.Mutex

// Stops reads and writes the stop records, by workload key.
type Stops struct{ Store *state.Store }

func (s Stops) load() (map[string]Stop, error) {
	all := map[string]Stop{}
	_, err := s.Store.Read(stopsFile, &struct {
		Stops *map[string]Stop `json:"stops"`
	}{&all})
	if errors.Is(err, fs.ErrNotExist) || errors.Is(err, state.ErrCorrupt) {
		return map[string]Stop{}, nil
	}
	if all == nil {
		all = map[string]Stop{}
	}
	return all, err
}

// Get returns key's record. An unreadable file reads as "not stopped".
func (s Stops) Get(key string) Stop {
	if s.Store == nil {
		return Stop{}
	}
	stopsMu.Lock()
	defer stopsMu.Unlock()
	all, err := s.load()
	if err != nil {
		return Stop{}
	}
	return all[key]
}

// Set replaces key's record; an empty one removes it.
func (s Stops) Set(key string, v Stop) error {
	stopsMu.Lock()
	defer stopsMu.Unlock()
	all, err := s.load()
	if err != nil {
		return err
	}
	if _, had := all[key]; !had && !v.Any() {
		return nil
	}
	if v.Any() {
		all[key] = v
	} else {
		delete(all, key)
	}
	return s.Store.Write(stopsFile, stopsVersion, struct {
		Stops map[string]Stop `json:"stops"`
	}{all})
}

// lockWait is how long an action waits for a deploy.
const lockWait = 20 * time.Second

// ErrBusy is an action's answer while a deploy runs.
var ErrBusy = errors.New("a deploy is running on this device; try again when it has finished")

// Lock is held by a deploy while it changes a workload, and by a workload
// action. One per agent (Deps.Workloads); safe for concurrent use.
type Lock struct{ c chan struct{} }

// NewLock returns an unlocked Lock.
func NewLock() *Lock { return &Lock{c: make(chan struct{}, 1)} }

// Acquire waits until ctx ends. The nil Lock never blocks (tests).
func (l *Lock) Acquire(ctx context.Context) (release func(), err error) {
	if l == nil {
		return func() {}, nil
	}
	select {
	case l.c <- struct{}{}:
		return func() { <-l.c }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// AcquireSoon is Acquire for an action: ErrBusy when a deploy holds it longer.
func (l *Lock) AcquireSoon(ctx context.Context) (func(), error) {
	wctx, cancel := context.WithTimeout(ctx, lockWait)
	defer cancel()
	release, err := l.Acquire(wctx)
	if err != nil && ctx.Err() == nil {
		return nil, ErrBusy
	}
	return release, err
}
