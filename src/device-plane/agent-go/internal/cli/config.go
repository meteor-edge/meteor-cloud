package cli

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"strings"

	"meteorcloud/meteorcli/internal/config"
)

// Environment variables the Python CLI read; kept so scripts keep working.
const (
	envDomain = "METEORCLI_DOMAIN"
	envAPIKey = "METEORCLI_API_KEY"
)

// runConfig is `meteorcli config`: store the server and API key (as root), show
// them, or import a Python install with `meteorcli config import`.
func runConfig(ctx context.Context, env *Env, args []string) int {
	if len(args) > 0 && args[0] == "import" {
		return runConfigImport(env, args[1:])
	}
	fl := newFlags(env, "config", "[--domain HOST] [--api-base URL] [--http] [--api-key KEY] [--show] | import")
	domain := fl.String("domain", "", "Server host or IP (meteorxx.com or 192.168.0.107:8000; or $"+envDomain+").")
	apiBase := fl.String("api-base", "", "Override the derived API origin (http:// is allowed for local testing).")
	useHTTP := fl.Bool("http", false, "Use HTTP instead of HTTPS (for local testing).")
	apiKey := fl.String("api-key", "", "Enrollment API key (or $"+envAPIKey+"). Stored with 0600 permissions.")
	show := fl.Bool("show", false, "Print the stored configuration (never prints secrets).")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	p := env.Paths
	if config.LooksLikePython(p.LegacyDir) {
		fmt.Fprintln(env.Stderr, "error: this device still has a Python meteorcli layout. Run 'sudo meteorcli config import' first.")
		return 1
	}
	if *show {
		return showConfig(env)
	}
	if *domain == "" {
		*domain = strings.TrimSpace(env.Getenv(envDomain))
	}
	key := normalizeSecret(*apiKey)
	if key == "" {
		key = normalizeSecret(env.Getenv(envAPIKey))
	}
	if *domain == "" && *apiBase == "" && key == "" {
		fmt.Fprintf(env.Stderr, "error: provide --domain, --api-base, and/or --api-key (or %s/%s).\n", envDomain, envAPIKey)
		return 2
	}

	c, err := config.Load(p.ConfigFile)
	switch {
	case errors.Is(err, fs.ErrNotExist), errors.Is(err, config.ErrCorrupt):
		c = &config.Config{}
	case err != nil:
		return failWrite(env, err)
	}
	before := c.Server()
	changed, err := c.SetServer(*domain, *apiBase, *useHTTP)
	if err != nil {
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
		return 2
	}
	var dropped []string
	// Credentials whose server is unknown (no or damaged config) may belong to
	// another server: treat setting a server then like a server change.
	if changed || (before == "" && c.Server() != "") {
		// Remove the old server's credentials before saving the new URL: if this is
		// interrupted, the next run still sees a server change and retries.
		if dropped, err = config.DropEnrollment(p); err != nil {
			return failWrite(env, err)
		}
	}
	if err := c.Save(p.ConfigFile, env.Owner); err != nil {
		return failWrite(env, err)
	}
	if key != "" {
		if err := writeSecret(env, p.APIKeyFile(), key); err != nil {
			return failWrite(env, err)
		}
	}

	server := c.Server()
	fmt.Fprintf(env.Stdout, "Configuration saved to %s\n", p.ConfigFile)
	if server != "" {
		fmt.Fprintf(env.Stdout, "API:            %s\n", server)
	}
	if server != "" && config.LANHostMissingPort(server) {
		flagName, value := "--domain", *domain
		if *apiBase != "" || value == "" {
			flagName, value = "--api-base", server
		}
		fmt.Fprintf(env.Stderr, "hint: laptop `make dev` listens on port 8000, not 80. Use 'meteorcli config %s %s:8000 …'\n",
			flagName, strings.TrimRight(value, "/"))
	}
	if len(dropped) > 0 {
		fmt.Fprintf(env.Stdout, "Cleared previous enrollment (%s); enroll again on this server.\n", strings.Join(dropped, ", "))
	}
	return 0
}

// writeSecret stores a secret in the identity directory (meteor, 0600).
func writeSecret(env *Env, path, value string) error {
	if err := env.Owner.EnsureDir(env.Paths.IdentityDir, 0o700); err != nil {
		return err
	}
	if err := config.WriteFileAtomic(path, []byte(value), 0o600); err != nil {
		return err
	}
	return env.Owner.Agent(path)
}

func showConfig(env *Env) int {
	p := env.Paths
	c, err := config.Load(p.ConfigFile)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		fmt.Fprintf(env.Stderr, "error: %v\n", err)
	}
	device, _ := config.LoadDevice(p.DeviceFile())
	fmt.Fprintf(env.Stdout, "Domain:         %s\n", orDash(fieldOf(c, func(c *config.Config) string { return c.Domain })))
	fmt.Fprintf(env.Stdout, "API base:       %s\n", orDash(c.Server()))
	fmt.Fprintf(env.Stdout, "API key:        %s\n", presence(p.APIKeyFile()))
	fmt.Fprintf(env.Stdout, "Device ID:      %s\n", orDash(deviceField(device, func(d *config.Device) string { return d.DeviceID })))
	fmt.Fprintf(env.Stdout, "Credential:     %s\n", presence(p.DeviceTokenFile()))
	if c == nil && presence(p.APIKeyFile()) != present {
		return 1
	}
	return 0
}

func runConfigImport(env *Env, args []string) int {
	fl := newFlags(env, "config import", "[--from DIR] [--force]")
	from := fl.String("from", env.Paths.LegacyDir, "Directory of the Python meteorcli install.")
	force := fl.Bool("force", false, "Overwrite files that already exist in the new layout.")
	if code, done := parseFlags(fl, args); done {
		return code
	}
	rep, err := config.ImportPython(*from, env.Paths, config.ImportOptions{Force: *force, Owner: env.Owner})
	for _, name := range rep.Imported {
		fmt.Fprintf(env.Stdout, "imported  %s\n", name)
	}
	for _, name := range rep.Skipped {
		fmt.Fprintf(env.Stdout, "skipped   %s\n", name)
	}
	if rep.Backup != "" {
		fmt.Fprintf(env.Stdout, "The Python config is kept as %s\n", rep.Backup)
	}
	if err != nil {
		return failWrite(env, err)
	}
	if len(rep.Skipped) > 0 && !*force {
		fmt.Fprintln(env.Stdout, "Skipped files stay where they were. Run again with --force to replace the existing ones.")
	}
	if len(rep.Imported) == 0 && len(rep.Skipped) == 0 {
		fmt.Fprintf(env.Stdout, "Nothing to import from %s\n", *from)
	}
	return 0
}

func fieldOf(c *config.Config, get func(*config.Config) string) string {
	if c == nil {
		return ""
	}
	return get(c)
}

func deviceField(d *config.Device, get func(*config.Device) string) string {
	if d == nil {
		return ""
	}
	return get(d)
}

func orDash(s string) string {
	if s == "" {
		return "—"
	}
	return s
}
