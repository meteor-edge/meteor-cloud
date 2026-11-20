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

// addUser creates a login user with a home directory, a password, optional
// passwordless sudo and an optional SSH key. A failure half way removes the
// user again, so a retry starts clean.
func (h Host) addUser(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (_ any, err error) {
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
	if _, err := h.LookupUser(p.Username); err == nil {
		return nil, localrpc.Errorf(localrpc.CodeFailed, "a user named %q already exists", p.Username)
	}
	sudoers := filepath.Join(h.SudoersDir, "90-meteorcli-"+p.Username)
	defer func() {
		// useradd can fail after it created the account (exit 12: no home), so
		// undo whenever the user exists now; it did not before.
		if _, lerr := h.LookupUser(p.Username); err != nil && lerr == nil {
			// Undo with a fresh context: ctx may be what failed.
			cctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), userdelTimeout)
			defer cancel()
			_, _ = h.Run(cctx, "userdel", "-r", p.Username)
			_ = os.Remove(sudoers)
		}
	}()
	if _, err := h.Run(ctx, "useradd", "-m", "-s", "/bin/bash", p.Username); err != nil {
		return nil, err
	}
	if err := h.chpasswd(ctx, p.Username, p.Password); err != nil {
		return nil, err
	}
	if p.Sudo {
		if err := writeNew(sudoers, p.Username+" ALL=(ALL) NOPASSWD:ALL\n", 0o440); err != nil {
			return nil, fmt.Errorf("sudoers: %w", err)
		}
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

// addAuthorizedKey appends key to ~/.ssh/authorized_keys of a user that was
// just created (so nothing in the home can be a planted link).
func addAuthorizedKey(info UserInfo, key string) error {
	if !filepath.IsAbs(info.Home) {
		return fmt.Errorf("the new user's home %q is not an absolute path", info.Home)
	}
	dir := filepath.Join(info.Home, ".ssh")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	path := filepath.Join(dir, "authorized_keys")
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if _, err := f.WriteString(key + "\n"); err != nil {
		f.Close()
		return fmt.Errorf("authorized_keys: %w", err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("authorized_keys: %w", err)
	}
	for _, p := range []string{dir, path} {
		if err := os.Lchown(p, info.UID, info.GID); err != nil {
			return fmt.Errorf("authorized_keys: %w", err)
		}
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
