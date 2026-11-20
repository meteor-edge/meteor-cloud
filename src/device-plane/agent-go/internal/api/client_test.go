package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// recordingsDir holds real exchanges of the Python agent (tests/agent-contract).
const recordingsDir = "../../../../../tests/agent-contract/recordings"

type exchange struct {
	Request struct {
		Method  string            `json:"method"`
		Path    string            `json:"path"`
		Headers map[string]string `json:"headers"`
		Body    map[string]any    `json:"body"`
	} `json:"request"`
	Response struct {
		Status int             `json:"status"`
		Body   json.RawMessage `json:"body"`
	} `json:"response"`
}

// call runs the client method that matches a recorded request. ok is false for
// routes this package does not cover yet.
func call(ctx context.Context, c *Client, ex exchange) (bool, error) {
	body := ex.Request.Body
	str := func(k string) string { s, _ := body[k].(string); return s }
	inventory := func(drop ...string) Inventory {
		inv := Inventory{}
		for k, v := range body {
			if !slices.Contains(drop, k) {
				inv[k] = v
			}
		}
		return inv
	}
	var err error
	switch ex.Request.Method + " " + ex.Request.Path {
	case "GET /api/v1/health":
		err = c.Health(ctx)
	case "POST /api/v1/agent/enroll/check":
		_, err = c.EnrollCheck(ctx, "key_x")
	case "POST /api/v1/agent/enroll/request":
		_, err = c.EnrollRequest(ctx, "key_x", inventory("name"), str("name"))
	case "POST /api/v1/agent/enroll/poll":
		_, err = c.EnrollPoll(ctx, str("request_id"), str("claim_secret"))
	case "POST /api/v1/agent/register":
		_, err = c.Register(ctx, str("token"), inventory("token", "name", "device_token"), str("name"), str("device_token"))
	case "POST /api/v1/agent/heartbeat":
		req := HeartbeatRequest{Inventory: inventory("capabilities", "metrics", "software_report")}
		if raw, ok := body["capabilities"]; ok {
			b, _ := json.Marshal(raw)
			req.Capabilities = &Capabilities{}
			_ = json.Unmarshal(b, req.Capabilities)
		}
		if raw, ok := body["software_report"].([]any); ok {
			for _, item := range raw {
				b, _ := json.Marshal(item)
				req.SoftwareReport = append(req.SoftwareReport, b)
			}
		}
		_, err = c.Heartbeat(ctx, "dev_x", req)
	case "GET /api/v1/agent/mqtt":
		_, err = c.MQTTStatus(ctx, "dev_x")
	case "POST /api/v1/agent/mqtt/credentials":
		_, err = c.MQTTCredentials(ctx, "dev_x")
	default:
		return false, nil
	}
	return true, err
}

func TestReplayPythonRecordings(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join(recordingsDir, "*.json"))
	if len(files) == 0 {
		t.Fatalf("no recordings in %s", recordingsDir)
	}
	covered := map[string]bool{}
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		// Placeholders stand in for numbers in a few places.
		data = []byte(strings.ReplaceAll(string(data), `"<mqtt_port>"`, "8883"))
		var rec struct {
			HTTP []exchange `json:"http"`
		}
		if err := json.Unmarshal(data, &rec); err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for i, ex := range rec.HTTP {
			name := filepath.Base(file) + "#" + ex.Request.Method + " " + ex.Request.Path
			var got *http.Request
			var gotBody map[string]any
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				got = r
				if b, _ := io.ReadAll(r.Body); len(b) > 0 {
					_ = json.Unmarshal(b, &gotBody)
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(ex.Response.Status)
				_, _ = w.Write(ex.Response.Body)
			}))
			c, _ := New(Options{BaseURL: srv.URL, Version: "1.2.3", HTTPClient: srv.Client()})
			ok, err := call(context.Background(), c, ex)
			srv.Close()
			if !ok {
				continue
			}
			covered[ex.Request.Method+" "+ex.Request.Path] = true
			if got == nil {
				t.Errorf("%s[%d]: no request", name, i)
				continue
			}
			if got.Method != ex.Request.Method || got.URL.Path != ex.Request.Path {
				t.Errorf("%s: sent %s %s", name, got.Method, got.URL.Path)
			}
			if got.Header.Get("User-Agent") != "meteorcli/1.2.3" || got.Header.Get(ProtocolHeader) != "1" {
				t.Errorf("%s: headers %v", name, got.Header)
			}
			_, wantAuth := ex.Request.Headers["authorization"]
			if (got.Header.Get("Authorization") != "") != wantAuth {
				t.Errorf("%s: Authorization sent = %v, recorded = %v", name, got.Header.Get("Authorization") != "", wantAuth)
			}
			if ex.Request.Body != nil && !sameKeys(gotBody, ex.Request.Body) {
				t.Errorf("%s: body keys %v, recorded %v", name, keys(gotBody), keys(ex.Request.Body))
			}
			isOK := ex.Response.Status >= 200 && ex.Response.Status < 300
			if isOK && err != nil {
				t.Errorf("%s: %v", name, err)
			}
			var apiErr *Error
			if !isOK && (!errors.As(err, &apiErr) || apiErr.Status != ex.Response.Status || apiErr.Code == "request_failed") {
				t.Errorf("%s: err = %v", name, err)
			}
		}
	}
	for _, route := range []string{
		"GET /api/v1/health", "POST /api/v1/agent/enroll/check", "POST /api/v1/agent/enroll/request",
		"POST /api/v1/agent/enroll/poll", "POST /api/v1/agent/register", "POST /api/v1/agent/heartbeat",
		"GET /api/v1/agent/mqtt", "POST /api/v1/agent/mqtt/credentials",
	} {
		if !covered[route] {
			t.Errorf("no recording exercised %s", route)
		}
	}
}

func sameKeys(a, b map[string]any) bool {
	return slices.Equal(keys(a), keys(b))
}

func keys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	slices.Sort(out)
	return out
}

func TestErrorsAndRetryAfter(t *testing.T) {
	tests := []struct {
		name        string
		status      int
		body        string
		retryAfter  string
		wantCode    string
		wantAuth    bool
		wantRetry   bool
		wantWaitSec int
	}{
		{"unauthorized", 401, `{"error":{"code":"invalid_token","message":"no"}}`, "", "invalid_token", true, false, 0},
		{"forbidden", 403, `{"error":{"code":"device_revoked","message":"no"}}`, "", "device_revoked", true, false, 0},
		{"rate limited", 429, `{"error":{"code":"rate_limited","message":"slow"}}`, "30", "rate_limited", false, true, 30},
		{"server error, html", 502, `<html>bad gateway</html>`, "", "request_failed", false, true, 0},
		{"not found", 404, `{"error":{"code":"not_found","message":"x"}}`, "soon", "not_found", false, false, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if tt.retryAfter != "" {
					w.Header().Set("Retry-After", tt.retryAfter)
				}
				w.WriteHeader(tt.status)
				_, _ = io.WriteString(w, tt.body)
			}))
			defer srv.Close()
			c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
			_, err := c.MQTTStatus(context.Background(), "dev_x")
			var e *Error
			if !errors.As(err, &e) || e.Code != tt.wantCode || e.Status != tt.status {
				t.Fatalf("err = %#v", err)
			}
			if IsAuth(err) != tt.wantAuth || Retryable(err) != tt.wantRetry || StatusOf(err) != tt.status {
				t.Errorf("IsAuth %v Retryable %v", IsAuth(err), Retryable(err))
			}
			if e.RetryAfter != time.Duration(tt.wantWaitSec)*time.Second {
				t.Errorf("RetryAfter %v", e.RetryAfter)
			}
		})
	}
}

func TestNotJSONAndNetworkErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "<html>captive portal</html>")
	}))
	c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
	_, err := c.MQTTStatus(context.Background(), "dev_x")
	var e *Error
	if !errors.As(err, &e) || e.Code != "bad_response" || !Retryable(err) {
		t.Errorf("captive portal: %v", err)
	}
	srv.Close()

	_, err = c.MQTTStatus(context.Background(), "dev_x")
	if !errors.As(err, &e) || e.Code != "network_error" || !Retryable(err) || IsAuth(err) {
		t.Errorf("closed server: %v", err)
	}
	if Retryable(errors.New("plain")) || StatusOf(errors.New("plain")) != 0 {
		t.Error("plain errors are not API errors")
	}
}

func TestRedirectIsNotFollowed(t *testing.T) {
	var hops int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hops++
		http.Redirect(w, r, "/elsewhere", http.StatusFound)
	}))
	defer srv.Close()
	c, err := New(Options{BaseURL: srv.URL})
	if err != nil {
		t.Fatal(err)
	}
	_, err = c.MQTTStatus(context.Background(), "dev_x")
	if StatusOf(err) != http.StatusFound || hops != 1 {
		t.Errorf("err %v hops %d", err, hops)
	}
}

func TestNewValidates(t *testing.T) {
	if _, err := New(Options{BaseURL: "meteor.example"}); err == nil {
		t.Error("missing scheme: want error")
	}
	if _, err := New(Options{BaseURL: "https://x", CAFile: "/does/not/exist"}); err == nil {
		t.Error("missing CA file: want error")
	}
	bad := filepath.Join(t.TempDir(), "ca.pem")
	_ = os.WriteFile(bad, []byte("not pem"), 0o600)
	if _, err := New(Options{BaseURL: "https://x", CAFile: bad}); err == nil {
		t.Error("CA without PEM: want error")
	}
	c, err := New(Options{BaseURL: "https://meteor.example/"})
	if err != nil || c.BaseURL() != "https://meteor.example" || c.UserAgent() != "meteorcli/0.0.0" {
		t.Errorf("New = %v, %v", c, err)
	}
}

func TestBackoff(t *testing.T) {
	b := &Backoff{Min: time.Second, Max: 8 * time.Second, Rand: func() float64 { return 0.999999 }}
	var got []int
	for range 6 {
		got = append(got, int(b.Next(nil).Round(time.Second)/time.Second))
	}
	if !slices.Equal(got, []int{1, 2, 4, 8, 8, 8}) {
		t.Errorf("delays %v", got)
	}
	b.Reset()
	b.Rand = func() float64 { return 0 }
	if d := b.Next(nil); d != 500*time.Millisecond {
		t.Errorf("jitter floor %v", d)
	}
	if d := b.Next(&Error{Status: 429, RetryAfter: 30 * time.Second}); d != 30*time.Second {
		t.Errorf("Retry-After ignored: %v", d)
	}
	if d := NewBackoff().Next(nil); d < 500*time.Millisecond || d >= time.Second {
		t.Errorf("default first delay %v", d)
	}
}

func TestCapabilitiesSendArrays(t *testing.T) {
	b, err := json.Marshal(HeartbeatRequest{Capabilities: &Capabilities{Systemd: true}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `"actions":[]`) || !strings.Contains(string(b), `"deploy_kinds":[]`) {
		t.Errorf("body %s", b)
	}
	kinds := []string{"systemd"}
	b, _ = json.Marshal(Capabilities{DeployKinds: kinds})
	if !strings.Contains(string(b), `"deploy_kinds":["systemd"]`) {
		t.Errorf("body %s", b)
	}
}

func TestEmptyBodyAndCutErrorBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/health" {
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/api/v1/agent/mqtt" {
			w.WriteHeader(http.StatusOK) // typed route, no body
			return
		}
		// 401 that promises more body than it sends.
		w.Header().Set("Content-Length", "100")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = io.WriteString(w, `{"error":`)
	}))
	defer srv.Close()
	c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
	ctx := context.Background()
	if err := c.Health(ctx); err != nil {
		t.Errorf("Health with an empty body: %v", err)
	}
	var e *Error
	if _, err := c.MQTTStatus(ctx, "dev_x"); !errors.As(err, &e) || e.Code != "bad_response" {
		t.Errorf("empty typed answer: %v", err)
	}
	if _, err := c.MQTTCredentials(ctx, "dev_x"); !IsAuth(err) {
		t.Errorf("cut 401 body lost the status: %v", err)
	}
}
