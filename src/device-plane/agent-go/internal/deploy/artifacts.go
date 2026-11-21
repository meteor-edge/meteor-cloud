package deploy

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/api"
)

// progressTimeout bounds one progress-only heartbeat.
const progressTimeout = 15 * time.Second

// artifactPath is where an artifact is cached; artifactPath+".sha256" holds its checksum.
func (c *cycle) artifactPath(id string) string { return filepath.Join(c.r.d.Paths.ArtifactsDir, id) }

// ensureArtifact downloads an artifact, or reuses the cached copy when its
// checksum file matches. expected is the signed manifest's artifact_sha256
// ("" when unsigned). Progress goes to the server while it downloads.
func (c *cycle) ensureArtifact(ctx context.Context, key, id, expected string) (string, error) {
	if !api.ValidArtifactID(id) {
		return "", fmt.Errorf("invalid artifact id %q", id)
	}
	dest := c.artifactPath(id)
	expected = strings.ToLower(expected)
	if cached(dest, expected) {
		return dest, nil
	}
	c.progress(ctx, key, 0)
	if c.refused {
		return "", errAuth
	}
	// A refused token stops the download and any further progress reports.
	dctx, cancel := context.WithCancel(ctx)
	defer cancel()
	bucket := -1
	onProgress := func(received, total int64) {
		percent := 0
		if total > 0 {
			percent = int(min(99, received*100/total))
		}
		if percent/10 == bucket || c.refused {
			return
		}
		bucket = percent / 10
		c.progress(ctx, key, percent)
		if c.refused {
			cancel()
		}
	}

	if err := os.MkdirAll(filepath.Dir(dest), 0o750); err != nil {
		return "", fmt.Errorf("artifact %s: %w", id, err)
	}
	tmp, err := os.CreateTemp(filepath.Dir(dest), "."+id+".partial-*")
	if err != nil {
		return "", fmt.Errorf("artifact %s: %w", id, err)
	}
	defer os.Remove(tmp.Name()) // gone after the rename; removes a partial download
	sum, err := c.r.d.API.Download(dctx, c.token, id, tmp, onProgress)
	if c.refused {
		return "", errAuth
	}
	if err == nil && expected != "" && sum != expected {
		err = &api.Error{Code: "checksum_mismatch",
			Message: fmt.Sprintf("Artifact %s does not match the signed checksum; refusing to install it.", id)}
	}
	if err == nil {
		err = errors.Join(tmp.Sync(), tmp.Chmod(0o640))
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(tmp.Name(), dest)
	}
	if err == nil {
		err = os.WriteFile(dest+".sha256", []byte(sum+"\n"), 0o640)
	}
	if err != nil {
		return "", err
	}
	c.r.d.Log.Info("downloaded artifact", "artifact", id, "workload", key)
	return dest, nil
}

// cached reports whether path holds an earlier download whose bytes still
// match its checksum file and expected (the checksum file alone when expected
// is "").
func cached(path, expected string) bool {
	if st, err := os.Stat(path); err != nil || !st.Mode().IsRegular() {
		return false
	}
	stored, err := os.ReadFile(path + ".sha256")
	if err != nil {
		return false
	}
	saved := strings.ToLower(strings.TrimSpace(string(stored)))
	if expected == "" {
		expected = saved
	}
	if len(saved) != 64 || saved != expected {
		return false
	}
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false
	}
	return hex.EncodeToString(h.Sum(nil)) == expected
}

// progress sends a heartbeat that carries only an "updating" report, as the
// Python agent did, so the console shows a download moving. It is best
// effort: the final result goes through the outbox.
func (c *cycle) progress(ctx context.Context, key string, percent int) {
	ctx, cancel := context.WithTimeout(ctx, progressTimeout)
	defer cancel()
	body, _ := jsonRaw(updating(key, percent))
	_, err := c.r.d.API.Heartbeat(ctx, c.token, api.HeartbeatRequest{SoftwareReport: body})
	switch {
	case api.IsAuth(err):
		c.authFailed()
	case err != nil:
		c.r.d.Log.Warn("could not report progress", "workload", key, "err", err)
	}
}
