package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Download limits. A large image on a slow link may take long, so the whole
// download gets downloadTimeout, but a transfer that stops moving is cut after
// downloadStall. Variables so tests can shorten them.
var (
	downloadTimeout = 30 * time.Minute
	downloadStall   = 5 * time.Minute
)

// redirectHeader tells the server this agent can follow a redirect to storage.
const redirectHeader = "X-Meteorcli-Redirect"

// checksumHeader carries the artifact's SHA-256 (hex).
const checksumHeader = "X-Checksum-SHA256"

// validArtifactID keeps an artifact id usable as a URL segment and a file name.
var validArtifactID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9-]{0,63}$`)

// ValidArtifactID reports whether id may be downloaded and cached under its name.
func ValidArtifactID(id string) bool { return validArtifactID.MatchString(id) }

// Download streams an artifact into w and returns its SHA-256 (hex). The server
// answers with the bytes, or with a redirect to object storage; the storage
// URL is fetched without the device token, never over http after https, and
// only when the redirect carried the checksum. A body that does not match the
// server's checksum fails with code checksum_mismatch. progress, when set, is
// called after each chunk with the bytes so far and the total (-1 if unknown).
func (c *Client) Download(ctx context.Context, deviceToken, artifactID string, w io.Writer, progress func(received, total int64)) (string, error) {
	base, shared := c.target()
	if base == "" {
		return "", ErrNoServer
	}
	// Never follow a redirect automatically: the token must not go to storage.
	noRedirect := *shared
	noRedirect.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	hc := &noRedirect
	if !ValidArtifactID(artifactID) {
		return "", &Error{Code: "bad_artifact_id", Message: fmt.Sprintf("invalid artifact id %q", artifactID)}
	}
	ctx, cancel := context.WithTimeout(ctx, downloadTimeout)
	defer cancel()
	stall := time.AfterFunc(downloadStall, cancel)
	defer stall.Stop()

	apiURL := base + "/api/v1/agent/artifacts/" + artifactID + "/download"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return "", fmt.Errorf("download %s: %w", artifactID, err)
	}
	req.Header.Set("Accept", "*/*")
	req.Header.Set("User-Agent", c.userAgent)
	req.Header.Set(ProtocolHeader, ProtocolVersion)
	req.Header.Set(redirectHeader, "1")
	req.Header.Set("Authorization", "Bearer "+deviceToken)
	resp, err := hc.Do(req)
	if err != nil {
		return "", &Error{Code: "network_error", Message: redactURLError(err, base)}
	}
	expected := ""
	fromStorage := false
	if isRedirect(resp.StatusCode) {
		resp, expected, err = c.followStorage(ctx, hc, apiURL, resp)
		if err != nil {
			return "", err
		}
		fromStorage = true
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		if fromStorage {
			// Storage refusing a presigned URL (an expired link, say) says
			// nothing about the device token: no Status, so IsAuth is false.
			// Its body is not read: it may echo the presigned URL.
			return "", &Error{Code: "storage_error", Message: fmt.Sprintf("Artifact storage answered HTTP %d.", resp.StatusCode)}
		}
		data, _ := io.ReadAll(io.LimitReader(resp.Body, maxBody))
		return "", decodeError(resp, data)
	}
	if expected == "" {
		expected = strings.ToLower(strings.TrimSpace(resp.Header.Get(checksumHeader)))
	}
	total := int64(-1)
	if n, err := strconv.ParseInt(resp.Header.Get("Content-Length"), 10, 64); err == nil && n >= 0 {
		total = n
	}

	digest := sha256.New()
	var received int64
	buf := make([]byte, 64<<10)
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			stall.Reset(downloadStall)
			if _, err := w.Write(buf[:n]); err != nil {
				return "", fmt.Errorf("download %s: %w", artifactID, err)
			}
			digest.Write(buf[:n])
			received += int64(n)
			if progress != nil {
				progress(received, total)
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			if ctx.Err() != nil {
				return "", &Error{Code: "network_error", Message: fmt.Sprintf("download %s stopped: %v", artifactID, ctx.Err())}
			}
			return "", &Error{Code: "network_error", Message: rerr.Error()}
		}
	}
	actual := hex.EncodeToString(digest.Sum(nil))
	if expected != "" && actual != expected {
		return "", &Error{Code: "checksum_mismatch", Message: "Downloaded artifact failed SHA-256 verification."}
	}
	return actual, nil
}

func isRedirect(status int) bool {
	switch status {
	case http.StatusMovedPermanently, http.StatusFound, http.StatusSeeOther,
		http.StatusTemporaryRedirect, http.StatusPermanentRedirect:
		return true
	}
	return false
}

// followStorage opens the storage URL a download redirected to, without the
// device token and without telling storage who we are. It returns the open
// response and the checksum the server sent with the redirect.
func (c *Client) followStorage(ctx context.Context, hc *http.Client, apiURL string, redirect *http.Response) (*http.Response, string, error) {
	location := redirect.Header.Get("Location")
	checksum := strings.ToLower(strings.TrimSpace(redirect.Header.Get(checksumHeader)))
	_, _ = io.Copy(io.Discard, io.LimitReader(redirect.Body, 64<<10))
	redirect.Body.Close()
	if location == "" {
		return nil, "", &Error{Status: redirect.StatusCode, Code: "download_redirect_invalid", Message: "The download redirect has no location."}
	}
	if len(checksum) != 64 {
		// Without the server's checksum the storage copy cannot be checked.
		return nil, "", &Error{Code: "checksum_missing", Message: "The download redirect did not include a SHA-256 checksum."}
	}
	from, err := url.Parse(apiURL)
	if err != nil {
		return nil, "", fmt.Errorf("download: %w", err)
	}
	ref, err := url.Parse(location)
	if err != nil {
		return nil, "", &Error{Code: "download_redirect_invalid", Message: "The download redirect location is not a URL."}
	}
	target := from.ResolveReference(ref)
	if (target.Scheme != "https" && target.Scheme != "http") || (target.Scheme == "http" && from.Scheme == "https") {
		return nil, "", &Error{Code: "download_redirect_insecure", Message: "Refusing to download over an insecure redirect."}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return nil, "", fmt.Errorf("download: %w", err)
	}
	req.Header.Set("Accept", "*/*")
	req.Header["User-Agent"] = []string{""} // send none: storage needs nothing about the device
	resp, err := hc.Do(req)
	if err != nil {
		// The URL is presigned: never put it in an error.
		msg := strings.ReplaceAll(err.Error(), target.String(), target.Scheme+"://"+target.Host)
		return nil, "", &Error{Code: "network_error", Message: msg}
	}
	return resp, checksum, nil
}
