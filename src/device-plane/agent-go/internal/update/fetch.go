package update

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Timeouts of the downloads.
const (
	smallTimeout    = 30 * time.Second
	binaryTimeout   = 10 * time.Minute
	smokeTimeout    = 20 * time.Second
	maxRedirects    = 5
	maxVersionBytes = 4 << 10
)

// Fetcher reads release files by name.
type Fetcher interface {
	// Get returns a small file, failing when it is larger than max.
	Get(ctx context.Context, name string, max int64) ([]byte, error)
	// Download writes the binary name to dest (mode 0755) and checks its size
	// and SHA-256 against f. dest is removed on any failure.
	Download(ctx context.Context, name string, f File, dest string) error
}

// HTTPFetcher reads a release from https://, http:// or file:// URLs. A
// release is trusted for its signature, not its transport, so http:// (a
// laptop serving dist/) and file:// (tests) are allowed; credentials are never
// sent.
type HTTPFetcher struct {
	Base      string
	UserAgent string
	// TLS is the client TLS config (TLS 1.2+, system CAs plus the pinned CA).
	TLS *tls.Config
	// Client replaces the HTTP client (tests).
	Client *http.Client
}

// NewHTTPFetcher checks base and returns a fetcher for it: a folder URL
// without credentials, query or fragment. The error never repeats the URL (it
// may hold a password).
func NewHTTPFetcher(base, userAgent string, tlsConfig *tls.Config) (*HTTPFetcher, error) {
	u, err := url.Parse(strings.TrimRight(strings.TrimSpace(base), "/"))
	switch {
	case err != nil || (u.Scheme != "https" && u.Scheme != "http" && u.Scheme != "file") || (u.Scheme != "file" && u.Host == ""):
		return nil, errors.New("the release URL must be an https://, http:// or file:// URL")
	case u.User != nil:
		return nil, errors.New("the release URL must not hold a user name or password")
	case u.RawQuery != "" || u.ForceQuery || u.Fragment != "":
		return nil, errors.New("the release URL must be a folder, without ?query or #fragment")
	}
	return &HTTPFetcher{Base: u.String(), UserAgent: userAgent, TLS: tlsConfig}, nil
}

func (f *HTTPFetcher) client() *http.Client {
	if f.Client != nil {
		return f.Client
	}
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.TLSClientConfig = f.TLS
	return &http.Client{Transport: tr, CheckRedirect: checkRedirect}
}

// checkRedirect follows a few redirects (GitHub release files redirect to
// their storage), but never from https down to http on any hop, and never to
// a URL with credentials (Go would send them as Basic auth).
func checkRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= maxRedirects {
		return errors.New("too many redirects")
	}
	if via[len(via)-1].URL.Scheme == "https" && req.URL.Scheme != "https" {
		return fmt.Errorf("refusing a redirect from https to %s", req.URL.Scheme)
	}
	if req.URL.User != nil {
		return errors.New("refusing a redirect to a URL with credentials")
	}
	return nil
}

// open returns the body of one release file.
func (f *HTTPFetcher) open(ctx context.Context, name string) (io.ReadCloser, error) {
	base, err := url.Parse(f.Base)
	if err != nil {
		return nil, err
	}
	if base.Scheme == "file" {
		return os.Open(filepath.Join(base.Path, name))
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base.JoinPath(name).String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", f.UserAgent)
	resp, err := f.client().Do(req)
	if err != nil {
		return nil, fmt.Errorf("download %s: %w", name, err)
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		return nil, fmt.Errorf("download %s: HTTP %d", name, resp.StatusCode)
	}
	return resp.Body, nil
}

// Get implements Fetcher.
func (f *HTTPFetcher) Get(ctx context.Context, name string, max int64) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, smallTimeout)
	defer cancel()
	body, err := f.open(ctx, name)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	data, err := io.ReadAll(io.LimitReader(body, max+1))
	if err != nil {
		return nil, fmt.Errorf("download %s: %w", name, err)
	}
	if int64(len(data)) > max {
		return nil, fmt.Errorf("download %s: larger than %d bytes", name, max)
	}
	return data, nil
}

// Download implements Fetcher.
func (f *HTTPFetcher) Download(ctx context.Context, name string, want File, dest string) (err error) {
	ctx, cancel := context.WithTimeout(ctx, binaryTimeout)
	defer cancel()
	body, err := f.open(ctx, name)
	if err != nil {
		return err
	}
	defer body.Close()
	return writeChecked(body, want, dest)
}

// writeChecked copies at most want.Size+1 bytes of r to dest, then checks the
// size and SHA-256 before the file is made executable.
func writeChecked(r io.Reader, want File, dest string) (err error) {
	_ = os.Remove(dest)
	out, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o700)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = os.Remove(dest)
		}
	}()
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(out, h), io.LimitReader(r, want.Size+1))
	if err == nil {
		err = out.Sync()
	}
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return fmt.Errorf("download %s: %w", filepath.Base(dest), err)
	}
	if n != want.Size {
		return fmt.Errorf("download: got %d bytes, the signed release says %d", n, want.Size)
	}
	if got := hex.EncodeToString(h.Sum(nil)); got != want.SHA256 {
		return fmt.Errorf("download: checksum mismatch (sha256 %s, the signed release says %s)", got, want.SHA256)
	}
	return os.Chmod(dest, 0o755)
}

// SmokeVersion runs path --version with a timeout and returns the version it
// printed ("meteorcli 1.2.3" gives "1.2.3").
func SmokeVersion(ctx context.Context, path string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, smokeTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "--version")
	cmd.Env = []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
	// A child that keeps stdout open must not hold Run past the timeout.
	cmd.WaitDelay = 2 * time.Second
	out := &capped{max: maxVersionBytes}
	cmd.Stdout = out
	if err := cmd.Run(); err != nil {
		return "", err
	}
	line := strings.TrimSpace(out.String())
	if !strings.HasPrefix(line, "meteorcli ") {
		return "", fmt.Errorf("--version printed %q", line)
	}
	return strings.TrimPrefix(line, "meteorcli "), nil
}

// FreeBytes reports the space available to root in dir.
func FreeBytes(dir string) (uint64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(dir, &st); err != nil {
		return 0, err
	}
	return st.Bfree * uint64(st.Bsize), nil
}

// linkOrCopy makes dst the same file as src: a hard link, or a copy where
// links are not possible.
func linkOrCopy(src, dst string) error {
	if err := os.Link(src, dst); err == nil {
		return nil
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(dst)
		return err
	}
	if err := out.Sync(); err != nil {
		out.Close()
		os.Remove(dst)
		return err
	}
	return out.Close()
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

// capped keeps the first max bytes written and drops the rest.
type capped struct {
	bytes.Buffer
	max int
}

func (c *capped) Write(p []byte) (int, error) {
	if room := c.max - c.Len(); room > 0 {
		c.Buffer.Write(p[:min(len(p), room)])
	}
	return len(p), nil
}
