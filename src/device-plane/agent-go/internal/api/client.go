// Package api is the typed HTTP client for the control plane's /api/v1/agent/*
// routes (contracts/device-agent-v1.md). It knows the wire format and nothing else:
// it never stores credentials and never decides what a 401 means (identity does).
package api

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// ProtocolHeader carries the contract version this agent speaks.
const ProtocolHeader = "X-Meteor-Agent-Protocol"

// ProtocolVersion is the device agent contract version. Additive changes keep 1.
const ProtocolVersion = "1"

// DefaultTimeout bounds one API call. Downloads use their own, longer timeout.
const DefaultTimeout = 15 * time.Second

// maxBody stops a misbehaving proxy from making the agent read without end.
const maxBody = 4 << 20

// Options configures a Client.
type Options struct {
	// BaseURL is the API origin, for example https://meteor.example.
	BaseURL string
	// Version is this agent's version, sent as User-Agent: meteorcli/<Version>.
	Version string
	// CAFile is an optional PEM file trusted in addition to the system CAs.
	CAFile string
	// Timeout per call; zero means DefaultTimeout.
	Timeout time.Duration
	// HTTPClient replaces the default transport (tests).
	HTTPClient *http.Client
}

// Client calls the agent API. It is safe for concurrent use.
type Client struct {
	base      string
	userAgent string
	timeout   time.Duration
	http      *http.Client
}

// New builds a Client with TLS 1.2+, the system CAs plus the optional pinned CA,
// and redirects turned off so a bearer token never follows one.
func New(o Options) (*Client, error) {
	base := strings.TrimRight(strings.TrimSpace(o.BaseURL), "/")
	if !strings.HasPrefix(base, "https://") && !strings.HasPrefix(base, "http://") {
		return nil, fmt.Errorf("api: base URL %q must start with https:// or http://", o.BaseURL)
	}
	version := o.Version
	if version == "" {
		version = "0.0.0"
	}
	timeout := o.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	hc := o.HTTPClient
	if hc == nil {
		tlsConfig, err := tlsConfig(o.CAFile)
		if err != nil {
			return nil, err
		}
		tr := http.DefaultTransport.(*http.Transport).Clone()
		tr.TLSClientConfig = tlsConfig
		tr.MaxIdleConns = 2
		tr.IdleConnTimeout = 90 * time.Second
		hc = &http.Client{
			Transport:     tr,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		}
	}
	return &Client{base: base, userAgent: "meteorcli/" + version, timeout: timeout, http: hc}, nil
}

func tlsConfig(caFile string) (*tls.Config, error) {
	pool, err := x509.SystemCertPool()
	if err != nil || pool == nil {
		pool = x509.NewCertPool()
	}
	if caFile != "" {
		pem, err := os.ReadFile(caFile)
		if err != nil {
			return nil, fmt.Errorf("api: pinned CA: %w", err)
		}
		if !pool.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("api: pinned CA %s holds no PEM certificate", caFile)
		}
	}
	return &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool}, nil
}

// BaseURL is the origin this client talks to.
func (c *Client) BaseURL() string { return c.base }

// UserAgent is the User-Agent header this client sends.
func (c *Client) UserAgent() string { return c.userAgent }

// Error is a failed call. Status 0 means the server was not reached or did not
// answer with JSON; retry those like a network failure.
type Error struct {
	Status  int
	Code    string
	Message string
	// RetryAfter is the server's Retry-After on 429/503, zero when absent.
	RetryAfter time.Duration
}

func (e *Error) Error() string {
	if e.Status == 0 {
		return fmt.Sprintf("%s: %s", e.Code, e.Message)
	}
	return fmt.Sprintf("HTTP %d %s: %s", e.Status, e.Code, e.Message)
}

// IsAuth reports a 401 or 403: the credential is invalid or revoked. Only the
// identity module acts on it; everyone else just stops the current cycle.
func IsAuth(err error) bool {
	var e *Error
	return errors.As(err, &e) && (e.Status == http.StatusUnauthorized || e.Status == http.StatusForbidden)
}

// Retryable reports errors worth trying again later: network failures, 429 and 5xx.
func Retryable(err error) bool {
	var e *Error
	if !errors.As(err, &e) {
		return false
	}
	return e.Status == 0 || e.Status == http.StatusTooManyRequests || e.Status >= 500
}

// StatusOf returns the HTTP status of err, or 0.
func StatusOf(err error) int {
	var e *Error
	if errors.As(err, &e) {
		return e.Status
	}
	return 0
}

// do sends one JSON request and decodes a JSON answer into out (when not nil).
func (c *Client) do(ctx context.Context, method, path, bearer string, body, out any) error {
	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("%s %s: %w", method, path, err)
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, reader)
	if err != nil {
		return fmt.Errorf("%s %s: %w", method, path, err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)
	req.Header.Set(ProtocolHeader, ProtocolVersion)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return &Error{Code: "network_error", Message: redactURLError(err, c.base)}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return &Error{Code: "network_error", Message: err.Error()}
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return decodeError(resp, data)
	}
	if out == nil || len(bytes.TrimSpace(data)) == 0 {
		return nil
	}
	if err := json.Unmarshal(data, out); err != nil {
		// A captive portal or proxy answered instead of MeteorCloud.
		return &Error{Code: "bad_response", Message: fmt.Sprintf("%s did not answer with JSON", path)}
	}
	return nil
}

func decodeError(resp *http.Response, data []byte) *Error {
	e := &Error{
		Status:  resp.StatusCode,
		Code:    "request_failed",
		Message: fmt.Sprintf("Request failed with status %d", resp.StatusCode),
	}
	var payload struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(data, &payload) == nil {
		if payload.Error.Code != "" {
			e.Code = payload.Error.Code
		}
		if payload.Error.Message != "" {
			e.Message = payload.Error.Message
		}
	}
	if s := strings.TrimSpace(resp.Header.Get("Retry-After")); s != "" {
		if n, err := strconv.Atoi(s); err == nil && n >= 0 {
			e.RetryAfter = time.Duration(n) * time.Second
		}
	}
	return e
}

// redactURLError keeps the error short: the URL is known, the cause is what matters.
func redactURLError(err error, base string) string {
	msg := err.Error()
	return strings.ReplaceAll(msg, base, "")
}
