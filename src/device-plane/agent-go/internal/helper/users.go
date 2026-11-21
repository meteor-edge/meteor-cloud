package helper

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
)

// Rules copied from the Python meteorcli-action helper.
var (
	// usernameRe is a portable Linux user name; it cannot start with "-".
	usernameRe = regexp.MustCompile(`^[a-z_][a-z0-9_-]{0,31}$`)
	// sshKeyRe is one SSH public key line of a type OpenSSH accepts.
	sshKeyRe = regexp.MustCompile(`^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|` +
		`sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}( [^\r\n]*)?$`)
)

// reservedUsers can never be changed from the console.
var reservedUsers = map[string]bool{
	"root": true, "daemon": true, "bin": true, "sys": true, "sync": true, "nobody": true,
	"sshd": true, "systemd-network": true, "meteor": true,
}

// usersMu serializes users.add and users.set_password so overlapping adds cannot
// roll back each other's account.
var usersMu sync.Mutex

// UserInfo is what the user operations need to know about an account.
type UserInfo struct {
	UID, GID int
	Home     string
}

// errNoUser is returned by Host.LookupUser for an unknown account.
var errNoUser = errors.New("no such user")

// SetPasswordRequest is the params of users.set_password. Password is a secret.
type SetPasswordRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

// AddUserRequest is the params of users.add. Password is a secret.
type AddUserRequest struct {
	Username      string `json:"username"`
	Password      string `json:"password"`
	Sudo          bool   `json:"sudo"`
	AuthorizedKey string `json:"authorized_key"`
}

func badRequest(format string, args ...any) error {
	return localrpc.Errorf(localrpc.CodeBadRequest, format, args...)
}

func validUsername(name string) error {
	if !usernameRe.MatchString(name) {
		return badRequest("username must start with a lowercase letter or _ and use a-z, 0-9, _ or -")
	}
	if reservedUsers[name] {
		return badRequest("the account %q cannot be changed from the console", name)
	}
	return nil
}

func validPassword(pw string) error {
	if n := len([]rune(pw)); n < 8 || n > 128 {
		return badRequest("password must be 8 to 128 characters")
	}
	for _, r := range pw {
		if r < 32 || r == 127 {
			return badRequest("password must not contain line breaks or control characters")
		}
	}
	return nil
}

func validKey(key string) (string, error) {
	key = strings.TrimSpace(key)
	if key == "" {
		return "", nil
	}
	if len(key) > 4096 || !sshKeyRe.MatchString(key) {
		return "", badRequest("authorized_key must be one SSH public key line")
	}
	return key, nil
}

// auditUser logs the account name, never the password or key.
func auditUser(raw json.RawMessage) []any {
	var p struct {
		Username string `json:"username"`
		Sudo     bool   `json:"sudo"`
	}
	_ = json.Unmarshal(raw, &p)
	return []any{"username", p.Username, "sudo", p.Sudo}
}

// setPassword changes an existing user's password with chpasswd. The password
// goes on stdin, never on a command line.
func (h Host) setPassword(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	usersMu.Lock()
	defer usersMu.Unlock()
	var p SetPasswordRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if err := validUsername(p.Username); err != nil {
		return nil, err
	}
	if err := validPassword(p.Password); err != nil {
		return nil, err
	}
	if _, err := h.LookupUser(p.Username); err != nil {
		return nil, localrpc.Errorf(localrpc.CodeFailed, "there is no user named %q on this device", p.Username)
	}
	if err := h.chpasswd(ctx, p.Username, p.Password); err != nil {
		return nil, err
	}
	return map[string]bool{"changed": true}, nil
}

func (h Host) chpasswd(ctx context.Context, name, password string) error {
	if _, err := h.RunInput(ctx, []byte(name+":"+password+"\n"), "chpasswd"); err != nil {
		return fmt.Errorf("chpasswd for %s failed: %w", name, err)
	}
	return nil
}

// homePath is where useradd -m puts the home directory for name.
func (h Host) homePath(name string) string {
	if h.HomeDir != nil {
		return h.HomeDir(name)
	}
	return filepath.Join("/home", name)
}

func (h Host) lstat(path string) (os.FileInfo, error) {
	if h.Lstat != nil {
		return h.Lstat(path)
	}
	return os.Lstat(path)
}

// addUser creates a login user with a home directory, a password, optional
// passwordless sudo and an optional SSH key. A failure half way removes the
// user again, so a retry starts clean. The password is set last so the account
// stays locked until setup finishes.
func (h Host) addUser(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (_ any, err error) {
	usersMu.Lock()
	defer usersMu.Unlock()
	var p AddUserRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	if err := validUsername(p.Username); err != nil {
		return nil, err
	}
	if err := validPassword(p.Password); err != nil {
		return nil, err
	}
	key, err := validKey(p.AuthorizedKey)
	if err != nil {
		return nil, err
	}
	switch _, err := h.LookupUser(p.Username); {
	case err == nil:
		return nil, localrpc.Errorf(localrpc.CodeFailed, "a user named %q already exists", p.Username)
	case !errors.Is(err, errNoUser):
		return nil, fmt.Errorf("look up user %q: %w", p.Username, err)
	}
	homePath := h.homePath(p.Username)
	_, homeStatErr := h.lstat(homePath)
	homeExisted := homeStatErr == nil
	sudoers := filepath.Join(h.SudoersDir, "90-meteorcli-"+p.Username)
	created, sudoersWritten := false, false
	defer func() {
		if err == nil || !created {
			return
		}
		// Undo with a fresh context: ctx may be what failed. Only remove a home
		// this call created (useradd -m reuses an existing directory).
		cctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), userdelTimeout)
		defer cancel()
		args := []string{p.Username}
		if !homeExisted {
			args = append([]string{"-r"}, args...)
		}
		if _, delErr := h.Run(cctx, "userdel", args...); delErr != nil {
			err = errors.Join(err, fmt.Errorf("rollback userdel: %w", delErr))
		}
		if sudoersWritten {
			if rmErr := os.Remove(sudoers); rmErr != nil && !errors.Is(rmErr, os.ErrNotExist) {
				err = errors.Join(err, fmt.Errorf("rollback sudoers: %w", rmErr))
			}
		}
	}()
	if _, err := h.Run(ctx, "useradd", "-m", "-s", "/bin/bash", p.Username); err != nil {
		// useradd can fail after it created the account (exit 12: no home).
		if _, lerr := h.LookupUser(p.Username); lerr == nil {
			created = true
		}
		return nil, err
	}
	created = true
	if p.Sudo {
		if err := writeNew(sudoers, p.Username+" ALL=(ALL) NOPASSWD:ALL\n", 0o440); err != nil {
			return nil, fmt.Errorf("sudoers: %w", err)
		}
		sudoersWritten = true
	}
	if key != "" {
		info, err := h.LookupUser(p.Username)
		if err != nil {
			return nil, fmt.Errorf("look up the new user: %w", err)
		}
		if err := addAuthorizedKey(info, key); err != nil {
			return nil, err
		}
	}
	// Password last: the account stays locked until keys and sudoers are in place.
	if err := h.chpasswd(ctx, p.Username, p.Password); err != nil {
		return nil, err
	}
	return map[string]bool{"created": true}, nil
}

// userdelTimeout bounds the clean-up after a failed users.add.
const userdelTimeout = 30 * time.Second

// writeNew creates path (never follows or replaces an existing file).
func writeNew(path, content string, mode os.FileMode) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		return err
	}
	if _, err := f.WriteString(content); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// addAuthorizedKey appends key under the user's home using os.Root so path
// components cannot be replaced by symlinks that escape the home.
func addAuthorizedKey(info UserInfo, key string) error {
	if !filepath.IsAbs(info.Home) {
		return fmt.Errorf("the new user's home %q is not an absolute path", info.Home)
	}
	st, err := os.Lstat(info.Home)
	if err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("authorized_keys: home %q is not a real directory", info.Home)
	}
	root, err := os.OpenRoot(info.Home)
	if err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	defer root.Close()
	if err := root.Mkdir(".ssh", 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	sshDir, err := root.Open(".ssh")
	if err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if err := sshDir.Chown(info.UID, info.GID); err != nil {
		sshDir.Close()
		return fmt.Errorf("authorized_keys: %w", err)
	}
	sshDir.Close()
	f, err := root.OpenFile(".ssh/authorized_keys", os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if err := f.Chown(info.UID, info.GID); err != nil {
		f.Close()
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if _, err := f.WriteString(key + "\n"); err != nil {
		f.Close()
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	return nil
}

// lookupUser reads the system user database.
func lookupUser(name string) (UserInfo, error) {
	u, err := user.Lookup(name)
	if err != nil {
		var unknown user.UnknownUserError
		if errors.As(err, &unknown) {
			return UserInfo{}, errNoUser
		}
		return UserInfo{}, err
	}
	uid, err1 := strconv.Atoi(u.Uid)
	gid, err2 := strconv.Atoi(u.Gid)
	if err1 != nil || err2 != nil {
		return UserInfo{}, fmt.Errorf("user %s has a non-numeric uid or gid", name)
	}
	return UserInfo{UID: uid, GID: gid, Home: u.HomeDir}, nil
}

// ValidSSHUser checks a name for config.json's ssh_user by the rule the
// helper applies before it touches that account's authorized_keys.
func ValidSSHUser(name string) error { return validUsername(name) }
