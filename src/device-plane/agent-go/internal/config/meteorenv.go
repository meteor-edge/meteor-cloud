package config

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// BootDirEnv names a directory searched for a meteorenv file before /boot/firmware and /boot.
const BootDirEnv = "METEOR_BOOT_DIR"

// meteorenvNames are the first-boot settings files the Python agent and the
// first-boot scripts read, in order of preference.
var meteorenvNames = []string{".meteorenv", "meteorenv", "meteorenv.txt", ".meteorenv.txt"}

// meteorenvLine is one KEY=VALUE line.
var meteorenvLine = regexp.MustCompile(`^([A-Za-z_][A-Za-z0-9_]*)=(.*)$`)

// Meteorenv is the enrollment part of a first-boot settings file. APIKey is a
// secret. Wi-Fi, user and SSH settings in the same file belong to the
// first-boot scripts, not to meteorcli.
type Meteorenv struct {
	Domain  string
	APIBase string
	APIKey  string
	Name    string
}

// Empty reports whether the file had nothing meteorcli can use.
func (m Meteorenv) Empty() bool { return m.Domain == "" && m.APIBase == "" && m.APIKey == "" }

// FindMeteorenv returns the meteorenv files on the boot partition, best first.
// bootDir (from $METEOR_BOOT_DIR) is searched before /boot/firmware and /boot.
// Every directory is moved under root, like the other paths.
func FindMeteorenv(root, bootDir string) []string {
	dirs := []string{"/boot/firmware", "/boot"}
	if strings.TrimSpace(bootDir) != "" {
		dirs = append([]string{strings.TrimSpace(bootDir)}, dirs...)
	}
	var found []string
	seen := map[string]bool{}
	for _, dir := range dirs {
		for _, name := range meteorenvNames {
			p := filepath.Join(root, dir, name)
			if seen[p] {
				continue
			}
			if st, err := os.Stat(p); err == nil && st.Mode().IsRegular() {
				seen[p] = true
				found = append(found, p)
			}
		}
	}
	return found
}

// ParseMeteorenv reads KEY=VALUE lines like the first-boot scripts: comments
// and blank lines are skipped, one pair of matching quotes is removed.
func ParseMeteorenv(text string) map[string]string {
	text = strings.TrimPrefix(text, "\ufeff")
	out := map[string]string{}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		m := meteorenvLine.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		v := strings.TrimSpace(m[2])
		if len(v) >= 2 && (v[0] == '"' || v[0] == '\'') && v[len(v)-1] == v[0] {
			v = v[1 : len(v)-1]
		}
		out[m[1]] = v
	}
	return out
}

// ReadMeteorenv returns the enrollment settings of one meteorenv file.
func ReadMeteorenv(path string) (Meteorenv, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Meteorenv{}, err
	}
	v := ParseMeteorenv(string(data))
	return Meteorenv{
		Domain:  strings.TrimSpace(v["METEOR_DOMAIN"]),
		APIBase: strings.TrimSpace(v["METEOR_API_BASE"]),
		APIKey:  strings.TrimSpace(v["METEOR_API_KEY"]),
		Name:    strings.TrimSpace(v["METEOR_NAME"]),
	}, nil
}
