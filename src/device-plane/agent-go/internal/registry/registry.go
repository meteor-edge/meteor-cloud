// Package registry signs a developer in to MeteorCloud's container registry
// and pushes and pulls images with the docker CLI. It is for people and CI;
// devices get their own short-lived credentials (internal/deploy).
package registry

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var (
	hostRe  = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?$`)
	realmRe = regexp.MustCompile(`(?i)^Bearer\s.*\brealm="([^"]+)"`)
	// pathRe is a repository path and tag inside the organization's namespace.
	pathRe = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?$`)
	// localRe is an image known to the local engine.
	localRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}$`)
)

// Account is a stored sign-in. Token is a secret.
type Account struct {
	Host      string `json:"host"`
	Namespace string `json:"namespace"`
	Access    string `json:"access"`
	Token     string `json:"token"`
}

// Image returns the full name of path ("web:1.0") in the account's namespace.
// A name that is full already must be in that namespace: like Docker, a first
// part with a dot (or "localhost") is a registry host.
func (a Account) Image(path string) (string, error) {
	prefix := a.Host + "/" + a.Namespace + "/"
	name, full := strings.CutPrefix(path, prefix)
	if !pathRe.MatchString(name) {
		return "", fmt.Errorf("%q is not an image name such as web:1.0", path)
	}
	first, _, nested := strings.Cut(name, "/")
	if !full && nested && (strings.Contains(first, ".") || first == "localhost" || first == a.Host) {
		return "", fmt.Errorf("%q is not in %s", path, strings.TrimSuffix(prefix, "/"))
	}
	return prefix + name, nil
}

// insecure reports whether host is this machine, where plain HTTP is fine.
func insecure(host string) bool {
	name := host
	if h, _, err := net.SplitHostPort(host); err == nil {
		name = h
	}
	return name == "localhost" || name == "127.0.0.1"
}

// SignIn checks the token with the registry at host and returns the account.
func SignIn(ctx context.Context, host, token string) (Account, error) {
	host = strings.TrimSuffix(strings.TrimPrefix(host, "https://"), "/")
	if !hostRe.MatchString(host) {
		return Account{}, fmt.Errorf("%q is not a registry host such as registry.example.com", host)
	}
	scheme := "https://"
	if insecure(host) {
		scheme = "http://"
	}
	client := &http.Client{
		Timeout: 30 * time.Second,
		// A redirect must not carry the token elsewhere.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, scheme+host+"/v2/", nil)
	if err != nil {
		return Account{}, err
	}
	res, err := client.Do(req)
	if err != nil {
		return Account{}, err
	}
	res.Body.Close()
	m := realmRe.FindStringSubmatch(res.Header.Get("WWW-Authenticate"))
	if res.StatusCode != http.StatusUnauthorized || m == nil {
		return Account{}, fmt.Errorf("%s is not a MeteorCloud registry (HTTP %d)", host, res.StatusCode)
	}
	realm, err := url.Parse(m[1])
	if err != nil || !strings.HasSuffix(realm.Path, "/registry/token") ||
		(realm.Scheme != "https" && !(realm.Scheme == "http" && insecure(realm.Host))) {
		return Account{}, fmt.Errorf("%s names an unusable sign-in address", host)
	}
	realm.Path = strings.TrimSuffix(realm.Path, "token") + "account"
	realm.RawQuery = ""
	if req, err = http.NewRequestWithContext(ctx, http.MethodGet, realm.String(), nil); err != nil {
		return Account{}, err
	}
	req.SetBasicAuth("meteorcli", token)
	if res, err = client.Do(req); err != nil {
		return Account{}, err
	}
	defer res.Body.Close()
	switch res.StatusCode {
	case http.StatusOK:
	case http.StatusUnauthorized:
		return Account{}, errors.New("the registry token was refused")
	default:
		return Account{}, fmt.Errorf("sign-in failed (HTTP %d)", res.StatusCode)
	}
	var a Account
	if err := json.NewDecoder(io.LimitReader(res.Body, 1<<16)).Decode(&a); err != nil {
		return Account{}, fmt.Errorf("sign-in: %w", err)
	}
	if a.Host != host || !pathRe.MatchString(a.Namespace) {
		return Account{}, fmt.Errorf("the token belongs to registry %q, not %q", a.Host, host)
	}
	a.Token = token
	return a, nil
}

// Docker runs the docker CLI signed in as Account, through a private copy of
// the user's Docker settings that is removed afterwards. Run executes docker.
type Docker struct {
	Account   Account
	ConfigDir string
	Run       func(ctx context.Context, args ...string) error
}

// Push tags the local image as target and pushes it.
func (d Docker) Push(ctx context.Context, local, target string) error {
	if !localRe.MatchString(local) {
		return fmt.Errorf("%q is not an image", local)
	}
	if local != target {
		if err := d.Run(ctx, "tag", local, target); err != nil {
			return err
		}
	}
	return d.signedIn(ctx, "push", target)
}

// Pull pulls target.
func (d Docker) Pull(ctx context.Context, target string) error {
	return d.signedIn(ctx, "pull", target)
}

func (d Docker) signedIn(ctx context.Context, args ...string) error {
	dir, err := os.MkdirTemp("", "meteorcli-docker-*") // 0700
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	// The user's settings (proxies, the current context), with only this registry's sign-in.
	settings := map[string]any{}
	if data, err := os.ReadFile(filepath.Join(d.ConfigDir, "config.json")); err == nil {
		if err := json.Unmarshal(data, &settings); err != nil {
			return fmt.Errorf("%s: %w", filepath.Join(d.ConfigDir, "config.json"), err)
		}
	}
	if settings == nil { // the file says null
		settings = map[string]any{}
	}
	delete(settings, "credsStore")
	delete(settings, "credHelpers")
	auth := base64.StdEncoding.EncodeToString([]byte("meteorcli:" + d.Account.Token))
	settings["auths"] = map[string]any{d.Account.Host: map[string]string{"auth": auth}}
	data, err := json.Marshal(settings)
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), data, 0o600); err != nil {
		return err
	}
	if contexts, err := filepath.Abs(filepath.Join(d.ConfigDir, "contexts")); err == nil {
		if _, err := os.Stat(contexts); err == nil {
			if err := os.Symlink(contexts, filepath.Join(dir, "contexts")); err != nil {
				return err
			}
		}
	}
	return d.Run(ctx, append([]string{"--config", dir}, args...)...)
}

// Credential answers one docker credential helper request
// (docker-credential-meteorcli get|list|store|erase). Docker signs in through
// 'meteorcli login', so it cannot store other credentials here.
func Credential(s Store, action string, in io.Reader, out io.Writer) error {
	data, err := io.ReadAll(io.LimitReader(in, 1<<16))
	if err != nil {
		return err
	}
	host := strings.TrimSuffix(strings.TrimPrefix(strings.TrimSpace(string(data)), "https://"), "/")
	switch action {
	case "get":
		a, err := s.Get(host)
		if err != nil || host == "" {
			// The words Docker looks for to go on without a sign-in.
			return errors.New("credentials not found in native keychain")
		}
		return json.NewEncoder(out).Encode(map[string]string{"ServerURL": host, "Username": "meteorcli", "Secret": a.Token})
	case "list":
		all, err := s.List()
		if err != nil {
			return err
		}
		users := map[string]string{}
		for _, a := range all {
			users[a.Host] = "meteorcli"
		}
		return json.NewEncoder(out).Encode(users)
	case "erase":
		if host == "" {
			return errors.New("no server given")
		}
		return s.Remove(host)
	case "store":
		return errors.New("use 'meteorcli login' to sign in")
	}
	return fmt.Errorf("unknown credential helper action %q", action)
}
