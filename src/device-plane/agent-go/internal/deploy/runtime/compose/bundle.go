package compose

import (
	"archive/zip"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Limits of a zip bundle, so a hostile or broken archive cannot fill the disk.
const (
	maxBundleFiles = 2000
	maxBundleBytes = 1 << 30
)

// composeNames are the file names looked for in a bundle, in this order.
var composeNames = []string{"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}

// materialize puts the artifact into dir and returns the compose file: a zip
// is unpacked, anything else is the compose file itself (compose.yaml).
func materialize(artifact, dir string) (string, error) {
	zr, err := zip.OpenReader(artifact)
	if err != nil {
		if _, statErr := os.Stat(artifact); statErr != nil {
			return "", runtime.Failf("Compose artifact not found: %s", artifact)
		}
		// Not a zip: a plain compose file.
		data, err := os.ReadFile(artifact)
		if err != nil {
			return "", fmt.Errorf("compose artifact: %w", err)
		}
		dest := filepath.Join(dir, "compose.yaml")
		if err := config.WriteFileAtomic(dest, data, 0o600); err != nil {
			return "", fmt.Errorf("compose artifact: %w", err)
		}
		return dest, nil
	}
	defer zr.Close()
	if err := unzip(&zr.Reader, dir); err != nil {
		return "", err
	}
	for _, name := range composeNames {
		path := filepath.Join(dir, name)
		if st, err := os.Lstat(path); err == nil && st.Mode().IsRegular() {
			return path, nil
		}
	}
	return "", runtime.Failf("Compose zip must contain docker-compose.yml, docker-compose.yaml, compose.yml, or compose.yaml.")
}

// unzip extracts regular files and directories into dir. Entries that would
// land outside dir are refused; symlinks and devices are skipped.
func unzip(zr *zip.Reader, dir string) error {
	if len(zr.File) > maxBundleFiles {
		return runtime.Failf("Compose zip has more than %d files.", maxBundleFiles)
	}
	var total int64
	for _, f := range zr.File {
		name := filepath.FromSlash(f.Name)
		if name == "" || filepath.IsAbs(name) || strings.HasPrefix(filepath.Clean(name), "..") || strings.Contains(f.Name, `\`) {
			return runtime.Failf("Compose zip entry %q is outside the bundle.", f.Name)
		}
		dest := filepath.Join(dir, filepath.Clean(name))
		mode := f.Mode()
		switch {
		case mode.IsDir():
			if err := mkdirShared(dir, dest); err != nil {
				return err
			}
			continue
		case !mode.IsRegular():
			continue // a symlink or device: never created
		}
		if err := mkdirShared(dir, filepath.Dir(dest)); err != nil {
			return err
		}
		n, err := extract(f, dest, maxBundleBytes-total)
		if err != nil {
			return err
		}
		total += n
	}
	return nil
}

// extract writes one entry, at most limit bytes, replacing what is there
// (never following a symlink at dest).
func extract(f *zip.File, dest string, limit int64) (int64, error) {
	if st, err := os.Lstat(dest); err == nil && !st.Mode().IsRegular() {
		if err := os.RemoveAll(dest); err != nil {
			return 0, fmt.Errorf("compose zip: %w", err)
		}
	}
	src, err := f.Open()
	if err != nil {
		return 0, fmt.Errorf("compose zip %s: %w", f.Name, err)
	}
	defer src.Close()
	tmp, err := os.CreateTemp(filepath.Dir(dest), ".unzip-*")
	if err != nil {
		return 0, fmt.Errorf("compose zip: %w", err)
	}
	n, err := io.Copy(tmp, io.LimitReader(src, limit+1))
	if err == nil && n > limit {
		err = runtime.Failf("Compose zip is larger than %d bytes unpacked.", maxBundleBytes)
	}
	// Bundled files are read by containers that may not run as root.
	err = errors.Join(err, tmp.Chmod(0o644), tmp.Close())
	if err == nil {
		err = os.Rename(tmp.Name(), dest)
	}
	if err != nil {
		os.Remove(tmp.Name())
		return 0, err
	}
	return n, nil
}

// mkdirShared creates path and its parents below root with mode 0755 whatever
// the umask (the agent runs with 0077): containers that do not run as root
// read bundled files through these directories.
func mkdirShared(root, path string) error {
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == "." {
		return err
	}
	cur := root
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		cur = filepath.Join(cur, part)
		st, err := os.Lstat(cur)
		switch {
		case err == nil && st.IsDir():
		case err == nil:
			return runtime.Failf("Compose zip entry %s is a file and a directory.", part)
		default:
			if err := os.Mkdir(cur, 0o755); err != nil {
				return fmt.Errorf("compose zip: %w", err)
			}
		}
		if err := os.Chmod(cur, 0o755); err != nil {
			return fmt.Errorf("compose zip: %w", err)
		}
	}
	return nil
}
