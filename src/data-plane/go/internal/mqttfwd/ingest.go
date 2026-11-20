package mqttfwd

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

// StatusError is a non-2xx answer from the control plane.
type StatusError struct{ Status int }

func (e *StatusError) Error() string { return fmt.Sprintf("control-plane ingest HTTP %d", e.Status) }

// IngestClient calls POST /internal/mqtt/ingest with the same body and header
// as src/data-plane/data_plane/ingest.py.
type IngestClient struct {
	URL     string // CONTROL_PLANE_URL, without the path
	Token   string // MQTT_INTERNAL_TOKEN
	HTTP    *http.Client
	Timeout time.Duration
}

// NewIngestClient keeps up to conns connections to the control plane open, one
// per shard, so forwarding does not pay for a new TCP connection per message.
func NewIngestClient(baseURL, token string, conns int, timeout time.Duration) *IngestClient {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConns = conns
	t.MaxIdleConnsPerHost = conns
	return &IngestClient{URL: strings.TrimRight(baseURL, "/"), Token: token, HTTP: &http.Client{Transport: t}, Timeout: timeout}
}

type ingestBody struct {
	Topic   string `json:"topic"`
	Payload string `json:"payload"`
}

// Forward sends one message. A *StatusError means the control plane answered
// with a non-2xx status; any other error means it was not reached.
func (c *IngestClient) Forward(ctx context.Context, topic string, payload []byte) error {
	body, err := json.Marshal(ingestBody{Topic: topic, Payload: decodeUTF8(payload)})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, c.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL+"/internal/mqtt/ingest", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-MQTT-Internal-Token", c.Token)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	// Read the (tiny) body so the connection goes back to the pool.
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	resp.Body.Close()
	if resp.StatusCode >= 300 {
		return &StatusError{Status: resp.StatusCode}
	}
	return nil
}

// decodeUTF8 matches Python's payload.decode("utf-8", errors="replace"): each
// maximal invalid subpart (a lead byte plus the continuation bytes that could
// still have completed it, Unicode 3.9 table 3-7) becomes one U+FFFD.
func decodeUTF8(p []byte) string {
	if utf8.Valid(p) {
		return string(p)
	}
	var b strings.Builder
	b.Grow(len(p) + 8)
	for len(p) > 0 {
		r, size := utf8.DecodeRune(p)
		if r == utf8.RuneError && size == 1 {
			size = invalidSubpart(p)
		}
		b.WriteRune(r)
		p = p[size:]
	}
	return b.String()
}

// invalidSubpart is the length of the ill-formed sequence at the start of p:
// its lead byte and the continuation bytes that were still allowed after it.
func invalidSubpart(p []byte) int {
	need, lo, hi := 0, byte(0x80), byte(0xBF) // continuation bytes after the lead; range of the first one
	switch b := p[0]; {
	case b >= 0xC2 && b <= 0xDF:
		need = 1
	case b == 0xE0:
		need, lo = 2, 0xA0
	case b == 0xED:
		need, hi = 2, 0x9F
	case b >= 0xE1 && b <= 0xEF:
		need = 2
	case b == 0xF0:
		need, lo = 3, 0x90
	case b == 0xF4:
		need, hi = 3, 0x8F
	case b >= 0xF1 && b <= 0xF3:
		need = 3
	}
	n := 1
	for i := 0; i < need && n < len(p); i++ {
		if c := p[n]; c < lo || c > hi {
			break
		}
		n++
		lo, hi = 0x80, 0xBF
	}
	return n
}

// resultLabel is the "result" label of meteorcloud_dataplane_ingest_forward_total.
func resultLabel(err error) string {
	var se *StatusError
	switch {
	case err == nil:
		return "ok"
	case errors.As(err, &se):
		return "http_error"
	default:
		return "unreachable"
	}
}

// retry says whether a failed forward should be tried again. Answers that
// mean "the control plane is not able to take it right now" are retried for as
// long as it takes; a misconfigured token is retried too, so fixing it loses
// nothing. Server errors that may be caused by this message are retried up to
// maxAttempts; other 4xx answers will never succeed and are dropped at once.
func retry(err error, attempt, maxAttempts int) bool {
	var se *StatusError
	if !errors.As(err, &se) {
		return true // not reached: connection refused, timeout, DNS
	}
	switch se.Status {
	case http.StatusUnauthorized, http.StatusForbidden, http.StatusRequestTimeout, http.StatusTooManyRequests,
		http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	}
	if se.Status >= 500 {
		return attempt < maxAttempts
	}
	return false
}
