package cli

import (
	"context"
	"fmt"
	"os"

	"meteorcloud/meteorcli/internal/config"
)

// runConfigMeteorenv is `meteorcli config meteorenv`: apply a first-boot
// settings file (meteorenv.txt or .meteorenv on the boot partition) and remove
// it, as the Python service did. meteorcli.service runs it as root before the
// agent starts (ExecStartPre=+), because the agent may neither write
// /etc/meteorcli nor delete files on /boot. The agent then sees the saved API
// key at start and submits an enrollment request by itself.
//
// A device that is already enrolled leaves the file alone. Errors are printed
// but never stop the agent from starting.
func runConfigMeteorenv(ctx context.Context, env *Env, args []string) int {
	fl := newFlags(env, "config meteorenv", "")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	p := env.Paths
	files := config.FindMeteorenv(p.Root, env.Getenv(config.BootDirEnv))
	if len(files) == 0 {
		return 0
	}
	path := files[0]
	if config.LooksLikePython(p.LegacyDir) {
		fmt.Fprintf(env.Stderr, "meteorenv: %s found, but a Python meteorcli layout is in place; run 'meteorcli config import' first\n", path)
		return 0
	}
	if _, err := os.Stat(p.DeviceTokenFile()); err == nil {
		fmt.Fprintf(env.Stderr, "meteorenv: this device is already enrolled; %s is ignored\n", path)
		return 0
	}
	m, err := config.ReadMeteorenv(path)
	if err != nil {
		fmt.Fprintf(env.Stderr, "meteorenv: %v\n", err)
		return 1
	}
	if m.Empty() {
		return 0
	}
	key := normalizeSecret(m.APIKey)
	_, _, err = saveConfig(env, m.Domain, m.APIBase, false, key, func(c *config.Config) {
		if m.Name != "" {
			c.EnrollName = m.Name
		}
	})
	if err != nil {
		fmt.Fprintf(env.Stderr, "meteorenv: applying %s: %v\n", path, err)
		return 1
	}
	if err := os.Remove(path); err != nil {
		fmt.Fprintf(env.Stderr, "meteorenv: settings applied, but %s could not be removed: %v\n", path, err)
	}
	fmt.Fprintf(env.Stdout, "meteorenv: read enrollment settings from %s; approve this device in MeteorCloud when the request appears.\n", path)
	reloadAgent(ctx, env)
	return 0
}
