package mqttfwd

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

type fakeBroker struct {
	published []string
	watched   []string
	unwatched []string
	fail      error
}

func (b *fakeBroker) Publish(_ context.Context, topic, payload string, qos byte, retain bool) error {
	if b.fail != nil {
		return b.fail
	}
	b.published = append(b.published, topic+"|"+payload+"|"+string('0'+qos)+"|"+map[bool]string{true: "r", false: "-"}[retain])
	return nil
}
func (b *fakeBroker) Watch(_ context.Context, t string) error {
	b.watched = append(b.watched, t)
	return b.fail
}
func (b *fakeBroker) Unwatch(_ context.Context, t string) error {
	b.unwatched = append(b.unwatched, t)
	return b.fail
}

func newAPI(b Broker, metrics bool) (*API, http.Handler) {
	reg := prometheus.NewRegistry()
	a := &API{Token: "tok", Metrics: NewMetrics(reg)}
	if b != nil {
		a.Broker = b
	}
	if metrics {
		a.Registry = reg
	}
	return a, a.Handler()
}

func do(h http.Handler, method, path, body string, token bool) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if token {
		req.Header.Set("X-MQTT-Internal-Token", "tok")
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestAPI(t *testing.T) {
	b := &fakeBroker{}
	a, h := newAPI(b, true)
	for _, tc := range []struct {
		name, method, path, body string
		token                    bool
		status                   int
		resp                     string
	}{
		{"health", "GET", "/health", "", false, 200, `{"status":"ok"}`},
		{"publish needs token", "POST", "/v1/publish", `{"topic":"t","payload":"p"}`, false, 401, `{"detail":"Invalid MQTT internal token."}`},
		{"publish defaults", "POST", "/v1/publish", `{"topic":"devices/1/commands","payload":"{}"}`, true, 200, `{"ok":true}`},
		{"publish options", "POST", "/v1/publish", `{"topic":"a","payload":"b","qos":0,"retain":true}`, true, 200, `{"ok":true}`},
		{"publish bad qos", "POST", "/v1/publish", `{"topic":"a","payload":"b","qos":3}`, true, 422, ""},
		{"publish missing payload", "POST", "/v1/publish", `{"topic":"a"}`, true, 422, ""},
		{"publish not json", "POST", "/v1/publish", `nope`, true, 422, ""},
		{"publish trailing data", "POST", "/v1/publish", `{"topic":"a","payload":"b"} {"x":1}`, true, 422, ""},
		{"publish wrong type", "POST", "/v1/publish", `{"topic":"a","payload":1}`, true, 422, ""},
		{"unclean path redirects", "POST", "/nonce/../v1/publish", `{}`, true, 307, ""},
		{"watch", "POST", "/v1/subscriptions", `{"topic":"lab/temp"}`, true, 200, `{"ok":true}`},
		{"unwatch", "POST", "/v1/subscriptions/unwatch", `{"topic":"lab/temp"}`, true, 200, `{"ok":true}`},
		{"watch needs token", "POST", "/v1/subscriptions", `{"topic":"x"}`, false, 401, ""},
		{"watch missing topic", "POST", "/v1/subscriptions", `{}`, true, 422, ""},
		{"wrong method", "GET", "/v1/publish", "", true, 405, ""},
	} {
		rec := do(h, tc.method, tc.path, tc.body, tc.token)
		if rec.Code != tc.status {
			t.Errorf("%s: status %d, want %d (%s)", tc.name, rec.Code, tc.status, rec.Body)
		}
		if tc.resp != "" && strings.TrimSpace(rec.Body.String()) != tc.resp {
			t.Errorf("%s: body %s, want %s", tc.name, rec.Body, tc.resp)
		}
	}
	if strings.Join(b.published, ",") != "devices/1/commands|{}|1|-,a|b|0|r" {
		t.Fatalf("published %v", b.published)
	}
	if len(b.watched) != 1 || len(b.unwatched) != 1 {
		t.Fatalf("watched %v unwatched %v", b.watched, b.unwatched)
	}
	if v := testutil.ToFloat64(a.Metrics.Publish.WithLabelValues("ok")); v != 2 {
		t.Fatalf("publish ok = %v", v)
	}
	if v := testutil.ToFloat64(a.Metrics.HTTPRequests.WithLabelValues("POST", "/v1/publish", "200")); v != 2 {
		t.Fatalf("http_requests_total publish 200 = %v", v)
	}
	if v := testutil.ToFloat64(a.Metrics.HTTPRequests.WithLabelValues("POST", "/v1/publish", "307")); v != 1 {
		t.Fatalf("redirect must be labelled with the route, got %v", v)
	}
	if v := testutil.ToFloat64(a.Metrics.HTTPRequests.WithLabelValues("GET", "<unmatched>", "405")); v != 1 {
		t.Fatalf("unmatched = %v", v)
	}
	body := do(h, "GET", "/metrics", "", false).Body.String()
	for _, name := range []string{"meteorcloud_dataplane_mqtt_connected", "meteorcloud_dataplane_publish_total",
		"http_requests_total", "meteorcloud_dataplane_forward_queue_capacity"} {
		if !strings.Contains(body, name) {
			t.Errorf("/metrics lacks %s", name)
		}
	}
	if strings.Contains(body, `path="/health"`) {
		t.Error("/health must not be counted")
	}
}

func TestAPIUnavailable(t *testing.T) {
	_, h := newAPI(nil, false)
	if rec := do(h, "POST", "/v1/publish", `{"topic":"t","payload":"p"}`, true); rec.Code != 503 ||
		!strings.Contains(rec.Body.String(), "MQTT client is not running.") {
		t.Fatalf("no broker: %d %s", rec.Code, rec.Body)
	}
	if rec := do(h, "GET", "/metrics", "", false); rec.Code != 404 {
		t.Fatalf("METRICS_ENABLED=false must hide /metrics, got %d", rec.Code)
	}
	if rec := do(h, "GET", "/health", "", false); rec.Code != 200 {
		t.Fatalf("health %d", rec.Code)
	}

	a, h := newAPI(&fakeBroker{fail: errors.New("broker down")}, false)
	if rec := do(h, "POST", "/v1/publish", `{"topic":"t","payload":"p"}`, true); rec.Code != 503 {
		t.Fatalf("broker error: %d", rec.Code)
	}
	if v := testutil.ToFloat64(a.Metrics.Publish.WithLabelValues("error")); v != 1 {
		t.Fatalf("publish error = %v", v)
	}
}
