package registry

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

// storeVersion is the format of the store's file.
const storeVersion = 1

// Store keeps accounts in one private file, the newest sign-in first.
type Store struct{ Path string }

// storeFile is the file. Accounts are kept raw so that a rewrite does not lose
// keys this version does not know.
type storeFile struct {
	Version  int             `json:"version"`
	Accounts []config.Object `json:"accounts"`
}

// UserStore is the store of the user running the command.
func UserStore() (Store, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return Store{}, err
	}
	return Store{Path: filepath.Join(dir, "meteorcli", "registry.json")}, nil
}

// List returns the accounts, none when nobody signed in. A damaged file is
// moved aside, so signing in again works.
func (s Store) List() ([]Account, error) {
	_, _, all, err := s.read()
	if errors.Is(err, config.ErrCorrupt) {
		var unlock func()
		if unlock, err = s.lock(); err != nil {
			return nil, err
		}
		defer unlock()
		_, _, all, err = s.readLocked()
	}
	return all, err
}

// Get returns the account of host, or the newest one for "".
func (s Store) Get(host string) (Account, error) {
	all, err := s.List()
	if err != nil {
		return Account{}, err
	}
	for _, a := range all {
		if host == "" || a.Host == host {
			return a, nil
		}
	}
	return Account{}, errors.New("not signed in; run 'meteorcli login registry.example.com'")
}

// Put adds or replaces an account.
func (s Store) Put(a Account) error {
	data, err := json.Marshal(a)
	if err != nil {
		return err
	}
	var added config.Object
	if err := json.Unmarshal(data, &added); err != nil {
		return err
	}
	return s.replace(a.Host, added)
}

// Remove forgets host.
func (s Store) Remove(host string) error { return s.replace(host) }

// replace drops host's account and puts added first, as one step for every
// process that uses the store.
func (s Store) replace(host string, added ...config.Object) error {
	unlock, err := s.lock()
	if err != nil {
		return err
	}
	defer unlock()
	file, keep, all, err := s.readLocked()
	if err != nil {
		return err
	}
	kept := append([]config.Object{}, added...)
	for i, a := range all {
		if a.Host != host {
			kept = append(kept, file.Accounts[i])
		}
	}
	return config.WriteJSON(s.Path, storeFile{Version: max(file.Version, storeVersion), Accounts: kept}, keep, 0o600)
}

// read returns the file, all its keys, and its accounts in the same order.
func (s Store) read() (file storeFile, keep config.Object, all []Account, err error) {
	keep, err = config.ReadJSON(s.Path, &file)
	if errors.Is(err, fs.ErrNotExist) {
		return storeFile{}, nil, nil, nil
	}
	if err != nil {
		return storeFile{}, nil, nil, err
	}
	for _, raw := range file.Accounts {
		var a Account
		data, err := json.Marshal(raw)
		if err == nil {
			err = json.Unmarshal(data, &a)
		}
		if err != nil || a.Host == "" {
			return storeFile{}, nil, nil, fmt.Errorf("read %s: %w", s.Path, config.ErrCorrupt)
		}
		all = append(all, a)
	}
	return file, keep, all, nil
}

// readLocked is read under the lock; a damaged file is moved aside and reads as empty.
func (s Store) readLocked() (storeFile, config.Object, []Account, error) {
	file, keep, all, err := s.read()
	if errors.Is(err, config.ErrCorrupt) {
		if _, err := config.Quarantine(s.Path, time.Now()); err != nil {
			return storeFile{}, nil, nil, err
		}
		return storeFile{}, nil, nil, nil
	}
	return file, keep, all, err
}

// lock holds a file beside the store: the store itself is replaced by every write.
func (s Store) lock() (unlock func(), err error) {
	if err := os.MkdirAll(filepath.Dir(s.Path), 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(s.Path+".lock", os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	for {
		if err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); !errors.Is(err, syscall.EINTR) {
			break
		}
	}
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("lock %s: %w", s.Path, err)
	}
	return func() { f.Close() }, nil
}
