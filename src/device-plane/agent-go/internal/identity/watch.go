package identity

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// Watching is a fallback: the CLI asks the agent to reload over the socket
// after it writes, but a file written by another tool (an image builder, a
// `config import`, an editor) must be picked up too. Without inotify the files
// are checked every fallbackPoll instead.
const (
	watchMask    = syscall.IN_CLOSE_WRITE | syscall.IN_MOVED_TO | syscall.IN_MOVED_FROM | syscall.IN_DELETE | syscall.IN_CREATE
	settle       = 200 * time.Millisecond // let a burst of writes finish before reloading
	fallbackPoll = time.Minute
)

// watch asks for a reload whenever the identity directory or the directory of
// config.json changes, until ctx ends. A directory that cannot be watched
// (missing at start, say) is covered by checking the files every minute too.
func (s *Service) watch(ctx context.Context) {
	p := s.Source.files.p
	f, missed, err := openInotify(p.IdentityDir, filepath.Dir(p.ConfigFile))
	if err != nil {
		s.Log.Warn("cannot watch identity files; checking them every minute instead", "err", err)
		s.pollFiles(ctx)
		return
	}
	var poll <-chan time.Time
	if missed != nil {
		s.Log.Warn("cannot watch every identity directory; also checking the files every minute", "err", missed)
		t := time.NewTicker(fallbackPoll)
		defer t.Stop()
		poll = t.C
	}
	events := make(chan struct{}, 1)
	go func() { // owned: ends when f is closed below
		buf := make([]byte, 4096)
		for {
			if _, err := f.Read(buf); err != nil {
				close(events)
				return
			}
			select {
			case events <- struct{}{}:
			default:
			}
		}
	}()
	defer func() {
		f.Close()
		for range events { // wait for the reader to stop
		}
	}()
	for {
		select {
		case <-ctx.Done():
			return
		case <-poll:
			s.requestReload()
			continue
		case _, ok := <-events:
			if !ok {
				s.Log.Warn("identity watch ended; checking files every minute instead")
				s.pollFiles(ctx)
				return
			}
		}
		t := time.NewTimer(settle)
		select {
		case <-ctx.Done():
			t.Stop()
			return
		case <-t.C:
		}
		s.requestReload()
	}
}

// pollFiles asks for a reload every fallbackPoll until ctx ends.
func (s *Service) pollFiles(ctx context.Context) {
	t := time.NewTicker(fallbackPoll)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.requestReload()
		}
	}
}

// openInotify watches dirs and returns the inotify descriptor as a pollable
// file, so a Read blocks in the runtime poller and Close ends it. missed names
// the directories that could not be watched; err is set (and no file
// returned) when none could.
func openInotify(dirs ...string) (f *os.File, missed, err error) {
	fd, err := syscall.InotifyInit1(syscall.IN_CLOEXEC | syscall.IN_NONBLOCK)
	if err != nil {
		return nil, nil, err
	}
	watched := 0
	var errs []error
	for _, dir := range dirs {
		if _, err := syscall.InotifyAddWatch(fd, dir, watchMask); err != nil {
			errs = append(errs, &os.PathError{Op: "inotify_add_watch", Path: dir, Err: err})
			continue
		}
		watched++
	}
	if watched == 0 {
		syscall.Close(fd)
		return nil, nil, errors.Join(errs...)
	}
	return os.NewFile(uintptr(fd), "inotify"), errors.Join(errs...), nil
}
