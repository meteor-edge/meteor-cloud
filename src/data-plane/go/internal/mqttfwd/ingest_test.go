package mqttfwd

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestForwardSendsPythonCompatibleRequest(t *testing.T) {
	var got struct {
		path, token, ctype string
		body               map[string]any
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got.path, got.token, got.ctype = r.URL.Path, r.Header.Get("X-MQTT-Internal-Token"), r.Header.Get("Content-Type")
		json.NewDecoder(r.Body).Decode(&got.body)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	c := NewIngestClient(srv.URL+"/", "secret", 2, time.Second)
	if err := c.Forward(context.Background(), "devices/abc/status", []byte(`{"online":true}`)); err != nil {
		t.Fatal(err)
	}
	if got.path != "/internal/mqtt/ingest" || got.token != "secret" || got.ctype != "application/json" {
		t.Fatalf("request = %+v", got)
	}
	want := map[string]any{"topic": "devices/abc/status", "payload": `{"online":true}`}
	if len(got.body) != 2 || got.body["topic"] != want["topic"] || got.body["payload"] != want["payload"] {
		t.Fatalf("body = %v, want %v", got.body, want)
	}
}

func TestForwardErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	c := NewIngestClient(srv.URL, "t", 1, time.Second)
	err := c.Forward(context.Background(), "x", nil)
	var se *StatusError
	if !errors.As(err, &se) || se.Status != 503 || resultLabel(err) != "http_error" {
		t.Fatalf("err = %v", err)
	}
	srv.Close()
	err = c.Forward(context.Background(), "x", nil)
	if err == nil || resultLabel(err) != "unreachable" {
		t.Fatalf("closed server: err = %v label %s", err, resultLabel(err))
	}
	if resultLabel(nil) != "ok" {
		t.Fatal("nil error must be ok")
	}
}

func TestDecodeUTF8(t *testing.T) {
	for _, tc := range []struct {
		in   []byte
		want string
	}{
		{[]byte("plain"), "plain"},
		{[]byte("héllo"), "héllo"},
		{[]byte{'a', 0xff, 'b'}, "a�b"},
		{[]byte{0xff, 0xfe}, "��"},
		// Expected values are what Python 3.13 bytes.decode("utf-8", "replace") returns.
		{[]byte{0xe2, 0x82}, "\uFFFD"},
		{[]byte{0xe2, 0x82, 'x'}, "\uFFFDx"},
		{[]byte{0xf0, 0x9f, 0x98}, "\uFFFD"},
		{[]byte{0xf1, 0x80, 0x80}, "\uFFFD"},
		{[]byte{0xed, 0xa0, 0x80}, "\uFFFD\uFFFD\uFFFD"},
		{[]byte{0xe0, 0x80, 0x80}, "\uFFFD\uFFFD\uFFFD"},
		{[]byte{0xf4, 0x90, 0x80, 0x80}, "\uFFFD\uFFFD\uFFFD\uFFFD"},
		{[]byte{0xc0, 0xaf}, "\uFFFD\uFFFD"},
		{nil, ""},
	} {
		if got := decodeUTF8(tc.in); got != tc.want {
			t.Errorf("decodeUTF8(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestRetry(t *testing.T) {
	status := func(code int) error { return &StatusError{Status: code} }
	for _, tc := range []struct {
		name    string
		err     error
		attempt int
		want    bool
	}{
		{"unreachable", errors.New("connection refused"), 100, true},
		{"unauthorized keeps retrying", status(401), 100, true},
		{"forbidden", status(403), 100, true},
		{"too many requests", status(429), 100, true},
		{"unavailable", status(503), 100, true},
		{"bad gateway", status(502), 100, true},
		{"server error first", status(500), 1, true},
		{"server error at limit", status(500), 3, false},
		{"bad request", status(400), 1, false},
		{"unprocessable", status(422), 1, false},
		{"not found", status(404), 1, false},
	} {
		if got := retry(tc.err, tc.attempt, 3); got != tc.want {
			t.Errorf("%s: retry = %v, want %v", tc.name, got, tc.want)
		}
	}
}

func TestMessageKind(t *testing.T) {
	for topic, want := range map[string]string{
		"devices/1/status":          "status",
		"devices/1/metrics":         "metrics",
		"devices/1/commands/result": "command_result",
		"devices/1/events":          "other",
		"devices/1":                 "other",
		"lab/temp":                  "other",
	} {
		if got := messageKind(topic); got != want {
			t.Errorf("messageKind(%q) = %q, want %q", topic, got, want)
		}
	}
}
