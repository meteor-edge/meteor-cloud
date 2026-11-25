package cli

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"meteorcloud/meteorcli/internal/registry"
)

// CredentialHelper is the name Docker runs the credential helper under.
const CredentialHelper = "docker-credential-meteorcli"

const envRegistryToken = "METEORCLI_REGISTRY_TOKEN"

// stdin is replaced by tests.
var stdin io.Reader = os.Stdin

// docker runs the docker CLI with the user's terminal (replaced by tests).
var docker = func(ctx context.Context, env *Env, args ...string) error {
	path, err := exec.LookPath("docker")
	if err != nil {
		return errors.New("docker is not installed")
	}
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Stdout, cmd.Stderr = env.Stdout, env.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("docker %s failed", args[len(args)-2])
	}
	return nil
}

// runLogin is `meteorcli login HOST`: check a registry token and keep it for
// this user. The token is read from stdin or the environment, never from the
// command line.
func runLogin(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "login", "REGISTRY   (the token is read from stdin or $"+envRegistryToken+")")
	if err := fl.Parse(args); err != nil {
		return 2
	}
	if fl.NArg() != 1 {
		fl.Usage()
		return 2
	}
	token := normalizeSecret(env.Getenv(envRegistryToken))
	if token == "" {
		fmt.Fprint(env.Stderr, "Registry token (Settings → Registry in the console): ")
		line, err := readSecret(ctx, stdin, env.Stderr)
		if err != nil {
			return failRegistry(env, err)
		}
		token = normalizeSecret(line)
	}
	if token == "" {
		return failRegistry(env, errors.New("a registry token is required"))
	}
	account, err := registry.SignIn(ctx, fl.Arg(0), token)
	if err != nil {
		return failRegistry(env, err)
	}
	store, err := registry.UserStore()
	if err != nil {
		return failRegistry(env, err)
	}
	if err := store.Put(account); err != nil {
		return failRegistry(env, err)
	}
	fmt.Fprintf(env.Stdout, "Signed in to %s/%s (%s).\n", account.Host, account.Namespace, account.Access)
	return 0
}

// runLogout is `meteorcli logout [HOST]`.
func runLogout(_ context.Context, env *Env, args []string) int {
	fl := newFlags(env, "logout", "[REGISTRY]")
	if err := fl.Parse(args); err != nil {
		return 2
	}
	store, err := registry.UserStore()
	if err != nil {
		return failRegistry(env, err)
	}
	account, err := store.Get(fl.Arg(0))
	if err != nil {
		return failRegistry(env, err)
	}
	if err := store.Remove(account.Host); err != nil {
		return failRegistry(env, err)
	}
	fmt.Fprintf(env.Stdout, "Signed out of %s. The token stays valid until it is revoked in the console.\n", account.Host)
	return 0
}

// runPush is `meteorcli push LOCAL [NAME]`.
func runPush(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "push", "[--registry HOST] LOCAL_IMAGE [NAME:TAG]")
	host := fl.String("registry", "", "Registry to push to (default: the newest sign-in).")
	if err := fl.Parse(args); err != nil {
		return 2
	}
	if fl.NArg() < 1 || fl.NArg() > 2 {
		fl.Usage()
		return 2
	}
	d, err := signedInDocker(env, *host)
	if err != nil {
		return failRegistry(env, err)
	}
	local, name := fl.Arg(0), fl.Arg(1)
	if name == "" {
		if name = local; !strings.HasPrefix(local, d.Account.Host+"/") {
			name = local[strings.LastIndex(local, "/")+1:]
		}
	}
	target, err := d.Account.Image(name)
	if err == nil {
		err = d.Push(ctx, local, target)
	}
	if err != nil {
		return failRegistry(env, err)
	}
	fmt.Fprintf(env.Stdout, "Pushed %s\n", target)
	return 0
}

// runPull is `meteorcli pull NAME`.
func runPull(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "pull", "[--registry HOST] NAME:TAG")
	host := fl.String("registry", "", "Registry to pull from (default: the newest sign-in).")
	if err := fl.Parse(args); err != nil {
		return 2
	}
	if fl.NArg() != 1 {
		fl.Usage()
		return 2
	}
	d, err := signedInDocker(env, *host)
	if err != nil {
		return failRegistry(env, err)
	}
	target, err := d.Account.Image(fl.Arg(0))
	if err == nil {
		err = d.Pull(ctx, target)
	}
	if err != nil {
		return failRegistry(env, err)
	}
	return 0
}

// runCredential is the Docker credential helper: 'meteorcli credential get',
// or the binary linked as docker-credential-meteorcli.
func runCredential(_ context.Context, env *Env, args []string) int {
	if len(args) != 1 {
		fmt.Fprintln(env.Stderr, "Usage: meteorcli credential get|list|store|erase")
		return 2
	}
	store, err := registry.UserStore()
	if err == nil {
		err = registry.Credential(store, args[0], stdin, env.Stdout)
	}
	if err != nil {
		fmt.Fprintln(env.Stdout, err) // Docker reads the reason from stdout
		return 1
	}
	return 0
}

func signedInDocker(env *Env, host string) (registry.Docker, error) {
	store, err := registry.UserStore()
	if err != nil {
		return registry.Docker{}, err
	}
	account, err := store.Get(host)
	if err != nil {
		return registry.Docker{}, err
	}
	dir := env.Getenv("DOCKER_CONFIG")
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return registry.Docker{}, err
		}
		dir = filepath.Join(home, ".docker")
	}
	run := func(ctx context.Context, args ...string) error { return docker(ctx, env, args...) }
	return registry.Docker{Account: account, ConfigDir: dir, Run: run}, nil
}

func failRegistry(env *Env, err error) int {
	fmt.Fprintf(env.Stderr, "error: %v\n", err)
	return 1
}
