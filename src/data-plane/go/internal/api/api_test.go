package api

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/ingest"
	"meteorcloud/dataplane/internal/live"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

const (
	orgA = "aaaaaaaa-0000-0000-0000-000000000001"
	orgB = "bbbbbbbb-0000-0000-0000-000000000002"
	dev1 = "dddddddd-0000-0000-0000-000000000001"
	dev2 = "dddddddd-0000-0000-0000-000000000002"

	tokCollector = "mca_collector" // org A, device 1: publishes energy.*
	tokDetector  = "mca_detector"  // org A, device 2: subscribes/reads energy.*, receives commands
	tokOperator  = "mca_operator"  // org A, cloud: may command detector, nothing else
	tokOtherOrg  = "mca_other_org" // org B: same grants as detector, different organization
	tokNoGrants  = "mca_no_grants" // org A: no permissions at all
	internalTok  = "internal-secret"
)

type env struct {
	t        *testing.T
	public   *httptest.Server
	ops      *httptest.Server
	policy   *identity.Store
	store    *storage.Memory
	server   *Server
	commands *commands.Service
	snapshot *identity.Snapshot
}

func testSnapshot() *identity.Snapshot {
	d1, d2 := dev1, dev2
	exp := time.Now().Add(time.Hour)
	full := identity.Permissions{Subscribe: []string{"energy.*", "meteor.lifecycle"}, Read: []string{"energy.*"}, ReceiveCommands: true}
	return &identity.Snapshot{Version: "1",
		Applications: []identity.SnapshotApplication{
			{ID: "app-collector", OrganizationID: orgA, Slug: "collector", Permissions: identity.Permissions{Publish: []string{"energy.*"}}},
			{ID: "app-detector", OrganizationID: orgA, Slug: "detector", Permissions: full},
			{ID: "app-operator", OrganizationID: orgA, Slug: "operator", Permissions: identity.Permissions{SendCommands: []string{"detector"}}},
			{ID: "app-nogrants", OrganizationID: orgA, Slug: "nogrants"},
			{ID: "app-b", OrganizationID: orgB, Slug: "detector", Permissions: identity.Permissions{
				Publish: []string{"energy.*"}, Subscribe: []string{"energy.*"}, Read: []string{"energy.*"}, ReceiveCommands: true}},
		},
		Credentials: []identity.SnapshotCredential{
			{ID: "cred-collector", OrganizationID: orgA, ApplicationID: "app-collector", DeviceID: &d1, Environment: "production", TokenHash: identity.HashToken(tokCollector), ExpiresAt: exp},
			{ID: "cred-detector", OrganizationID: orgA, ApplicationID: "app-detector", DeviceID: &d2, Environment: "production", TokenHash: identity.HashToken(tokDetector), ExpiresAt: exp},
			{ID: "cred-operator", OrganizationID: orgA, ApplicationID: "app-operator", Environment: "production", TokenHash: identity.HashToken(tokOperator), ExpiresAt: exp},
			{ID: "cred-nogrants", OrganizationID: orgA, ApplicationID: "app-nogrants", Environment: "production", TokenHash: identity.HashToken(tokNoGrants), ExpiresAt: exp},
			{ID: "cred-b", OrganizationID: orgB, ApplicationID: "app-b", Environment: "production", TokenHash: identity.HashToken(tokOtherOrg), ExpiresAt: exp},
		}}
}

func newEnv(t *testing.T, mutate func(*Options)) *env {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	metrics := observability.NewMetrics()
	policy := identity.NewStore(0)
	snap := testSnapshot()
	policy.Apply(snap)
	store := storage.NewMemory()
	b := bus.NewMemory()
	hub := live.NewHub(metrics)
	pipeline := ingest.New(ingest.Config{}, store, b, metrics, log)
	pipeline.Start()
	cmds := commands.NewService(commands.Config{}, store, b, policy, metrics, log)
	b.Start(context.Background(), bus.Handlers{OnLive: hub.OnLive, OnCommand: hub.OnCommand})
	opts := Options{InternalToken: internalTok, Limits: envelope.DefaultLimits(), MaxBatch: 50, QueryMaxBuckets: 100}
	if mutate != nil {
		mutate(&opts)
	}
	server := NewServer(opts, policy, pipeline, store, hub, cmds, metrics, log)
	e := &env{t: t, public: httptest.NewServer(server.PublicHandler()), ops: httptest.NewServer(server.OpsHandler()),
		policy: policy, store: store, server: server, commands: cmds, snapshot: snap}
	t.Cleanup(func() {
		server.CloseSessions()
		e.public.Close()
		e.ops.Close()
		pipeline.Stop(context.Background())
	})
	return e
}

func (e *env) do(method, path, token string, body any) (int, map[string]any) {
	e.t.Helper()
	return e.request(e.public.URL, method, path, map[string]string{"Authorization": "Bearer " + token}, body)
}

func (e *env) request(base, method, path string, headers map[string]string, body any) (int, map[string]any) {
	e.t.Helper()
	var reader io.Reader
	if body != nil {
		switch b := body.(type) {
		case string:
			reader = strings.NewReader(b)
		default:
			raw, _ := json.Marshal(b)
			reader = bytes.NewReader(raw)
		}
	}
	req, _ := http.NewRequest(method, base+path, reader)
	for k, v := range headers {
		if v != "Bearer " {
			req.Header.Set(k, v)
		}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	raw, _ := io.ReadAll(resp.Body)
	json.Unmarshal(raw, &out)
	return resp.StatusCode, out
}

func errCode(body map[string]any) string {
	if e, ok := body["error"].(map[string]any); ok {
		return fmt.Sprint(e["code"])
	}
	return ""
}

func msg(stream string, data string, extra map[string]any) map[string]any {
	m := map[string]any{"stream": stream, "data": json.RawMessage(data)}
	for k, v := range extra {
		m[k] = v
	}
	return m
}

func (e *env) publish(token string, messages ...map[string]any) (int, map[string]any) {
	return e.do("POST", "/v1/messages", token, map[string]any{"messages": messages})
}

func results(body map[string]any) []string {
	var out []string
	for _, r := range body["results"].([]any) {
		out = append(out, fmt.Sprint(r.(map[string]any)["status"]))
	}
	return out
}

// ---- authentication ---------------------------------------------------------

func TestAuthentication(t *testing.T) {
	e := newEnv(t, nil)
	if code, body := e.do("GET", "/v1/whoami", "", nil); code != 401 || errCode(body) != "missing_credentials" {
		t.Fatalf("no token: %d %v", code, body)
	}
	for _, bad := range []string{"mca_wrong", "dev_something", "Bearer"} {
		if code, body := e.do("GET", "/v1/whoami", bad, nil); code != 401 || errCode(body) != "invalid_credentials" {
			t.Fatalf("%s: %d %v", bad, code, body)
		}
	}
	code, body := e.do("GET", "/v1/whoami", tokCollector, nil)
	if code != 200 || body["organization_id"] != orgA || body["application"] != "collector" || body["device_id"] != dev1 {
		t.Fatalf("whoami: %d %v", code, body)
	}
	if _, body := e.do("GET", "/v1/whoami", tokOperator, nil); body["device_id"] != nil {
		t.Fatalf("cloud instance must have a null device: %v", body)
	}
	// Revocation: the credential disappears from the next snapshot.
	next := testSnapshot()
	next.Version = "2"
	next.Credentials = next.Credentials[1:]
	e.policy.Apply(next)
	if code, _ := e.do("GET", "/v1/whoami", tokCollector, nil); code != 401 {
		t.Fatalf("revoked credential still accepted: %d", code)
	}
	// No policy at all: fail closed with 503, not 401, so clients retry.
	empty := newEnv(t, nil)
	empty.policy.SetClock(func() time.Time { return time.Now().Add(1000 * time.Hour) })
	*empty.policy = *identity.NewStore(0)
	if code, body := empty.do("GET", "/v1/whoami", tokCollector, nil); code != 503 || errCode(body) != "policy_unavailable" {
		t.Fatalf("no policy: %d %v", code, body)
	}
}

// ---- ingestion ---------------------------------------------------------------

func TestPublishStoresWithIdentityFromCredential(t *testing.T) {
	e := newEnv(t, nil)
	code, body := e.publish(tokCollector,
		msg("energy.power", `{"power":145.6}`, map[string]any{"id": "m-1", "organization_id": orgB, "device_id": "spoofed", "application_id": "spoofed"}))
	if code != 200 || results(body)[0] != "stored" {
		t.Fatalf("%d %v", code, body)
	}
	events, _, _ := e.store.QueryEvents(context.Background(), storage.Query{OrganizationID: orgA, Stream: "energy.power", Limit: 10})
	if len(events) != 1 {
		t.Fatalf("stored %d events", len(events))
	}
	ev := events[0]
	if ev.OrganizationID != orgA || ev.DeviceID != dev1 || ev.ApplicationID != "app-collector" || ev.Application != "collector" {
		t.Fatalf("identity fields must come from the credential: %+v", ev)
	}
	if leaked, _, _ := e.store.QueryEvents(context.Background(), storage.Query{OrganizationID: orgB, Stream: "energy.power", Limit: 10}); len(leaked) != 0 {
		t.Fatal("event stored under the organization named in the body")
	}
}

func TestPublishDuplicatesRejectionsAndLimits(t *testing.T) {
	e := newEnv(t, nil)
	code, body := e.publish(tokCollector,
		msg("energy.power", `{"p":1}`, map[string]any{"id": "a"}),
		msg("energy.power", `{"p":2}`, map[string]any{"id": "a"}),
		msg("water.flow", `{"f":1}`, nil),
		msg("energy.power", `[1,2]`, nil),
		msg("energy.power", `{"p":1}`, map[string]any{"time": "2999-01-01T00:00:00Z"}),
		msg("energy.power", `{"p":1}`, map[string]any{"kind": "event", "type": "energy.anomaly"}),
	)
	if code != 200 {
		t.Fatalf("%d %v", code, body)
	}
	if got := fmt.Sprint(results(body)); got != "[stored duplicate rejected rejected rejected stored]" {
		t.Fatalf("results: %s", got)
	}
	rej := body["results"].([]any)[2].(map[string]any)["error"].(map[string]any)
	if rej["code"] != "forbidden_stream" {
		t.Fatalf("rejection code: %v", rej)
	}
	if body["accepted"].(float64) != 3 || body["rejected"].(float64) != 3 {
		t.Fatalf("counts: %v", body)
	}
	// Retry of the whole request is idempotent.
	_, again := e.publish(tokCollector, msg("energy.power", `{"p":1}`, map[string]any{"id": "a"}))
	if results(again)[0] != "duplicate" {
		t.Fatalf("retry: %v", again)
	}

	if code, body := e.publish(tokNoGrants, msg("energy.power", `{"p":1}`, nil)); code != 200 || results(body)[0] != "rejected" {
		t.Fatalf("no grants: %d %v", code, body)
	}
	if code, body := e.do("POST", "/v1/messages", tokCollector, `{"messages":[]}`); code != 400 || errCode(body) != "no_messages" {
		t.Fatalf("empty: %d %v", code, body)
	}
	if code, body := e.do("POST", "/v1/messages", tokCollector, `not json`); code != 400 || errCode(body) != "invalid_json" {
		t.Fatalf("bad json: %d %v", code, body)
	}
	many := make([]map[string]any, 51)
	for i := range many {
		many[i] = msg("energy.power", `{"p":1}`, nil)
	}
	if code, body := e.publish(tokCollector, many...); code != 413 || errCode(body) != "batch_too_large" {
		t.Fatalf("batch limit: %d %v", code, body)
	}
}

func TestRateLimitIsPerCredential(t *testing.T) {
	e := newEnv(t, func(o *Options) { o.RateLimitPerSec, o.RateLimitBurst = 1, 5 })
	limited := false
	for i := 0; i < 10; i++ {
		code, body := e.publish(tokCollector, msg("energy.power", `{"p":1}`, nil))
		if code == 429 {
			if errCode(body) != "rate_limited" {
				t.Fatalf("%v", body)
			}
			limited = true
			break
		}
	}
	if !limited {
		t.Fatal("rate limit never applied")
	}
	if code, _ := e.do("GET", "/v1/whoami", tokDetector, nil); code != 200 {
		t.Fatalf("another credential must not be limited: %d", code)
	}
}

func TestStorageFailureIsReportedSoClientsRetry(t *testing.T) {
	e := newEnv(t, nil)
	e.store.FailInserts = 100
	code, body := e.publish(tokCollector, msg("energy.power", `{"p":1}`, map[string]any{"id": "x"}))
	if code != 503 || errCode(body) != "storage_unavailable" {
		t.Fatalf("%d %v", code, body)
	}
	e.store.FailInserts = 0
	if _, body := e.publish(tokCollector, msg("energy.power", `{"p":1}`, map[string]any{"id": "x"})); results(body)[0] != "stored" {
		t.Fatalf("retry after recovery: %v", body)
	}
}

// ---- historical queries -------------------------------------------------------

func seed(e *env) time.Time {
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)
	var batch []map[string]any
	for i := 0; i < 6; i++ {
		batch = append(batch, msg("energy.power", fmt.Sprintf(`{"power":%d,"site":"%s"}`, (i+1)*10, []string{"a", "b"}[i%2]),
			map[string]any{"id": fmt.Sprintf("e%d", i), "time": base.Add(time.Duration(i) * 20 * time.Second).Format(time.RFC3339)}))
	}
	if code, body := e.publish(tokCollector, batch...); code != 200 {
		e.t.Fatalf("seed: %d %v", code, body)
	}
	return base
}

func TestQueryHistoryPaginationAndFilters(t *testing.T) {
	e := newEnv(t, nil)
	base := seed(e)
	code, page := e.do("GET", "/v1/streams/energy.power/data?limit=4", tokDetector, nil)
	if code != 200 || len(page["events"].([]any)) != 4 || page["next_cursor"] == nil {
		t.Fatalf("page1: %d %v", code, page)
	}
	first := page["events"].([]any)[0].(map[string]any)
	if first["id"] != "e0" || first["device_id"] != dev1 || first["application"] != "collector" {
		t.Fatalf("first event: %v", first)
	}
	code, page2 := e.do("GET", "/v1/streams/energy.power/data?limit=4&cursor="+page["next_cursor"].(string), tokDetector, nil)
	if code != 200 || len(page2["events"].([]any)) != 2 || page2["next_cursor"] != nil {
		t.Fatalf("page2: %d %v", code, page2)
	}
	q := url.Values{"start": {base.Add(40 * time.Second).Format(time.RFC3339)}, "end": {base.Add(80 * time.Second).Format(time.RFC3339)}, "order": {"desc"}}
	_, ranged := e.do("GET", "/v1/streams/energy.power/data?"+q.Encode(), tokDetector, nil)
	if got := ranged["events"].([]any); len(got) != 2 || got[0].(map[string]any)["id"] != "e3" {
		t.Fatalf("range/order: %v", ranged)
	}
	_, filtered := e.do("GET", "/v1/streams/energy.power/data?filter="+url.QueryEscape(`{"site":"b"}`), tokDetector, nil)
	if len(filtered["events"].([]any)) != 3 {
		t.Fatalf("filter: %v", filtered)
	}
	_, byDevice := e.do("GET", "/v1/streams/energy.power/data?device_id="+dev2, tokDetector, nil)
	if len(byDevice["events"].([]any)) != 0 {
		t.Fatalf("device filter: %v", byDevice)
	}
	for path, want := range map[string]string{
		"/v1/streams/energy.power/data?limit=0":         "invalid_parameter",
		"/v1/streams/energy.power/data?limit=100000":    "invalid_parameter",
		"/v1/streams/energy.power/data?start=yesterday": "invalid_parameter",
		"/v1/streams/energy.power/data?cursor=%21%21":   "invalid_parameter",
		"/v1/streams/energy.power/data?order=sideways":  "invalid_parameter",
		"/v1/streams/Energy%20Power/data":               "invalid_stream",
		"/v1/streams/energy.power/data?filter=%5B1%5D":  "invalid_parameter",
	} {
		if code, body := e.do("GET", path, tokDetector, nil); code != 400 || errCode(body) != want {
			t.Errorf("%s: %d %v", path, code, body)
		}
	}
}

func TestQueryAuthorizationAndOrganizationIsolation(t *testing.T) {
	e := newEnv(t, nil)
	seed(e)
	// Publish permission is not read permission.
	if code, body := e.do("GET", "/v1/streams/energy.power/data", tokCollector, nil); code != 403 || errCode(body) != "forbidden_stream" {
		t.Fatalf("collector read: %d %v", code, body)
	}
	if code, _ := e.do("GET", "/v1/streams/water.flow/data", tokDetector, nil); code != 403 {
		t.Fatalf("ungranted stream: %d", code)
	}
	// Same grants, other organization: sees nothing of org A.
	code, body := e.do("GET", "/v1/streams/energy.power/data", tokOtherOrg, nil)
	if code != 200 || len(body["events"].([]any)) != 0 {
		t.Fatalf("organization isolation broken: %d %v", code, body)
	}
}

func TestAggregate(t *testing.T) {
	e := newEnv(t, nil)
	base := seed(e)
	q := url.Values{"aggregate": {"avg"}, "field": {"power"}, "bucket": {"1m"},
		"start": {base.Format(time.RFC3339)}, "end": {base.Add(5 * time.Minute).Format(time.RFC3339)}}
	code, body := e.do("GET", "/v1/streams/energy.power/data?"+q.Encode(), tokDetector, nil)
	if code != 200 {
		t.Fatalf("%d %v", code, body)
	}
	buckets := body["buckets"].([]any)
	if len(buckets) != 2 || buckets[0].(map[string]any)["value"].(float64) != 20 || buckets[1].(map[string]any)["value"].(float64) != 50 {
		t.Fatalf("buckets: %v", buckets)
	}
	// Bounded: a tiny bucket over a long window is refused, not executed.
	q.Set("bucket", "1s")
	q.Set("end", base.Add(24*time.Hour).Format(time.RFC3339))
	if code, body := e.do("GET", "/v1/streams/energy.power/data?"+q.Encode(), tokDetector, nil); code != 400 || errCode(body) != "too_many_buckets" {
		t.Fatalf("bucket bound: %d %v", code, body)
	}
	for _, bad := range []string{"aggregate=median&field=power", "aggregate=avg", "aggregate=avg&field=power&bucket=1ms"} {
		if code, _ := e.do("GET", "/v1/streams/energy.power/data?"+bad, tokDetector, nil); code != 400 {
			t.Errorf("%s: %d", bad, code)
		}
	}
}

// ---- live streaming ------------------------------------------------------------

type wsClient struct {
	t    *testing.T
	conn *websocket.Conn
}

func (e *env) dial(token string) *wsClient {
	e.t.Helper()
	u := "ws" + strings.TrimPrefix(e.public.URL, "http") + "/v1/stream"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, u, &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": {"Bearer " + token}}})
	if err != nil {
		e.t.Fatalf("dial: %v", err)
	}
	c := &wsClient{t: e.t, conn: conn}
	e.t.Cleanup(func() { conn.CloseNow() })
	if f := c.read(); f["type"] != "ready" {
		e.t.Fatalf("first frame must be ready: %v", f)
	}
	return c
}

func (c *wsClient) send(frame map[string]any) {
	raw, _ := json.Marshal(frame)
	if err := c.conn.Write(context.Background(), websocket.MessageText, raw); err != nil {
		c.t.Fatal(err)
	}
}

func (c *wsClient) read() map[string]any {
	c.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, raw, err := c.conn.Read(ctx)
	if err != nil {
		c.t.Fatalf("read: %v", err)
	}
	var out map[string]any
	json.Unmarshal(raw, &out)
	return out
}

// next skips platform lifecycle events, which arrive asynchronously.
func (c *wsClient) next() map[string]any {
	for {
		f := c.read()
		if f["type"] == "event" && f["event"].(map[string]any)["stream"] == "meteor.lifecycle" {
			continue
		}
		return f
	}
}

func TestLiveSubscriptionBetweenApplications(t *testing.T) {
	e := newEnv(t, nil)
	detector := e.dial(tokDetector)
	otherOrg := e.dial(tokOtherOrg)

	detector.send(map[string]any{"op": "subscribe", "id": "power", "stream": "energy.*"})
	if f := detector.next(); f["type"] != "subscribed" || f["id"] != "power" {
		t.Fatalf("subscribe: %v", f)
	}
	otherOrg.send(map[string]any{"op": "subscribe", "id": "power", "stream": "energy.*"})
	if f := otherOrg.next(); f["type"] != "subscribed" {
		t.Fatalf("other org subscribe: %v", f)
	}
	detector.send(map[string]any{"op": "subscribe", "id": "nope", "stream": "water.flow"})
	if f := detector.next(); f["type"] != "error" || f["code"] != "forbidden_stream" || f["id"] != "nope" {
		t.Fatalf("forbidden subscribe: %v", f)
	}
	detector.send(map[string]any{"op": "subscribe", "id": "wild", "stream": "**"})
	if f := detector.next(); f["type"] != "error" || f["code"] != "invalid_subscription" {
		t.Fatalf("unrestricted wildcard: %v", f)
	}

	e.publish(tokCollector, msg("energy.power", `{"power":145.6}`, map[string]any{"id": "live-1"}))
	f := detector.next()
	if f["type"] != "event" || f["subscription"] != "power" {
		t.Fatalf("event frame: %v", f)
	}
	ev := f["event"].(map[string]any)
	if ev["id"] != "live-1" || ev["application"] != "collector" || ev["device_id"] != dev1 || ev["data"].(map[string]any)["power"].(float64) != 145.6 {
		t.Fatalf("event: %v", ev)
	}
	// A redelivered duplicate is not sent twice; the next frame is the next event.
	e.publish(tokCollector, msg("energy.power", `{"power":1}`, map[string]any{"id": "live-1"}))
	e.publish(tokCollector, msg("energy.voltage", `{"v":230}`, map[string]any{"id": "live-2"}))
	if f := detector.next(); f["event"].(map[string]any)["id"] != "live-2" {
		t.Fatalf("duplicate was redistributed: %v", f)
	}

	detector.send(map[string]any{"op": "unsubscribe", "id": "power"})
	if f := detector.next(); f["type"] != "unsubscribed" {
		t.Fatalf("unsubscribe: %v", f)
	}
	e.publish(tokCollector, msg("energy.power", `{"power":2}`, map[string]any{"id": "live-3"}))
	detector.send(map[string]any{"op": "ping"})
	if f := detector.next(); f["type"] != "pong" {
		t.Fatalf("event delivered after unsubscribe: %v", f)
	}
	// The other organization saw none of it.
	otherOrg.send(map[string]any{"op": "ping"})
	if f := otherOrg.next(); f["type"] != "pong" {
		t.Fatalf("organization isolation broken on the live path: %v", f)
	}
}

func TestStreamRequiresCredentialAndDropsRevokedConnections(t *testing.T) {
	e := newEnv(t, nil)
	u := "ws" + strings.TrimPrefix(e.public.URL, "http") + "/v1/stream"
	_, resp, err := websocket.Dial(context.Background(), u, nil)
	if err == nil || resp == nil || resp.StatusCode != 401 {
		t.Fatalf("unauthenticated stream must be refused: %v", err)
	}
	if e.policy.StillValid(&identity.Identity{CredentialID: "cred-detector", OrganizationID: orgA}) != true {
		t.Fatal("precondition")
	}
	next := testSnapshot()
	next.Version = "2"
	next.Credentials = append(next.Credentials[:1], next.Credentials[2:]...)
	e.policy.Apply(next)
	if e.policy.StillValid(&identity.Identity{CredentialID: "cred-detector", OrganizationID: orgA}) {
		t.Fatal("revoked credential must not stay valid for open connections")
	}
}

func TestLifecycleEvents(t *testing.T) {
	e := newEnv(t, func(o *Options) { o.LifecycleEvents = true })
	watcher := e.dial(tokDetector)
	watcher.send(map[string]any{"op": "subscribe", "id": "life", "stream": "meteor.lifecycle"})
	deadline := time.Now().Add(5 * time.Second)
	subscribed := false
	for !subscribed && time.Now().Before(deadline) {
		subscribed = watcher.read()["type"] == "subscribed"
	}
	other := e.dial(tokOperator)
	_ = other
	for time.Now().Before(deadline) {
		f := watcher.read()
		if f["type"] != "event" {
			continue
		}
		ev := f["event"].(map[string]any)
		if ev["type"] == "app.connected" && ev["application"] == "operator" && ev["kind"] == "lifecycle" {
			return
		}
	}
	t.Fatal("no app.connected lifecycle event")
}

// ---- commands -------------------------------------------------------------------

func TestCommandFromOperatorApplicationToDetector(t *testing.T) {
	e := newEnv(t, nil)
	detector := e.dial(tokDetector)

	code, cmd := e.do("POST", "/v1/commands", tokOperator, map[string]any{"application": "detector", "name": "recalibrate",
		"payload": map[string]any{"threshold": 0.8}, "device_id": dev2})
	if code != 202 || cmd["state"] != "DISPATCHED" {
		t.Fatalf("create: %d %v", code, cmd)
	}
	id := cmd["id"].(string)
	f := detector.next()
	if f["type"] != "command" {
		t.Fatalf("command frame: %v", f)
	}
	got := f["command"].(map[string]any)
	if got["id"] != id || got["name"] != "recalibrate" || got["payload"].(map[string]any)["threshold"].(float64) != 0.8 {
		t.Fatalf("command: %v", got)
	}
	for _, step := range []struct{ status, state string }{{"received", "RECEIVED"}, {"executing", "EXECUTING"}, {"succeeded", "SUCCEEDED"}} {
		code, body := e.do("POST", "/v1/commands/"+id+"/ack", tokDetector, map[string]any{"status": step.status, "result": map[string]any{"ok": true}})
		if code != 200 || body["state"] != step.state {
			t.Fatalf("%s: %d %v", step.status, code, body)
		}
	}
	// The issuer follows the command to its result.
	code, final := e.do("GET", "/v1/commands/"+id, tokOperator, nil)
	if code != 200 || final["state"] != "SUCCEEDED" || final["result"].(map[string]any)["ok"] != true {
		t.Fatalf("issuer view: %d %v", code, final)
	}
}

func TestCommandAuthorization(t *testing.T) {
	e := newEnv(t, nil)
	// No send_commands grant.
	if code, body := e.do("POST", "/v1/commands", tokCollector, map[string]any{"application": "detector", "name": "x"}); code != 403 || errCode(body) != "forbidden_command" {
		t.Fatalf("ungranted sender: %d %v", code, body)
	}
	// Granted for detector only.
	if code, _ := e.do("POST", "/v1/commands", tokOperator, map[string]any{"application": "collector", "name": "x"}); code != 403 {
		t.Fatalf("other target: %d", code)
	}
	_, cmd := e.do("POST", "/v1/commands", tokOperator, map[string]any{"application": "detector", "name": "x"})
	id := cmd["id"].(string)
	// Only the target acknowledges; others cannot even read it.
	if code, _ := e.do("POST", "/v1/commands/"+id+"/ack", tokOperator, map[string]any{"status": "succeeded"}); code != 403 {
		t.Fatalf("issuer must not ack: %d", code)
	}
	if code, _ := e.do("POST", "/v1/commands/"+id+"/ack", tokOtherOrg, map[string]any{"status": "succeeded"}); code != 404 {
		t.Fatalf("other organization ack: %d", code)
	}
	if code, _ := e.do("GET", "/v1/commands/"+id, tokCollector, nil); code != 404 {
		t.Fatalf("unrelated application read: %d", code)
	}
	if code, _ := e.do("GET", "/v1/commands/"+id, tokOtherOrg, nil); code != 404 {
		t.Fatalf("other organization read: %d", code)
	}
	if code, body := e.do("POST", "/v1/commands/"+id+"/ack", tokDetector, map[string]any{"status": "exploded"}); code != 400 || errCode(body) != "invalid_command" {
		t.Fatalf("bad status: %d %v", code, body)
	}
	if code, _ := e.do("GET", "/v1/commands/pending", tokCollector, nil); code != 403 {
		t.Fatalf("pending without receive_commands: %d", code)
	}
}

func TestPendingCommandsAreDeliveredOnConnect(t *testing.T) {
	e := newEnv(t, nil)
	_, cmd := e.do("POST", "/v1/commands", tokOperator, map[string]any{"application": "detector", "name": "while-offline"})
	code, pending := e.do("GET", "/v1/commands/pending", tokDetector, nil)
	if code != 200 || len(pending["commands"].([]any)) != 1 {
		t.Fatalf("polling fallback: %d %v", code, pending)
	}
	detector := e.dial(tokDetector) // connects after the command was sent
	f := detector.next()
	if f["type"] != "command" || f["command"].(map[string]any)["id"] != cmd["id"] {
		t.Fatalf("pending command not delivered on connect: %v", f)
	}
}

// ---- internal API and broker hooks ------------------------------------------------

func (e *env) internal(method, path, token string, body any) (int, map[string]any) {
	return e.request(e.ops.URL, method, path, map[string]string{"X-Data-Plane-Internal-Token": token}, body)
}

func TestInternalAPI(t *testing.T) {
	e := newEnv(t, nil)
	body := map[string]any{"organization_id": orgA, "application_id": "app-detector", "name": "restart", "issued_by": "user:42", "idempotency_key": "k"}
	if code, _ := e.internal("POST", "/internal/v1/commands", "wrong", body); code != 401 {
		t.Fatalf("wrong internal token: %d", code)
	}
	if code, _ := e.internal("POST", "/internal/v1/commands", "", body); code != 401 {
		t.Fatalf("missing internal token: %d", code)
	}
	code, cmd := e.internal("POST", "/internal/v1/commands", internalTok, body)
	if code != 202 || cmd["issued_by"] != "user:42" {
		t.Fatalf("create: %d %v", code, cmd)
	}
	_, again := e.internal("POST", "/internal/v1/commands", internalTok, body)
	if again["id"] != cmd["id"] {
		t.Fatal("idempotency key must return the same command")
	}
	code, got := e.internal("GET", "/internal/v1/organizations/"+orgA+"/commands/"+cmd["id"].(string), internalTok, nil)
	if code != 200 || got["name"] != "restart" {
		t.Fatalf("get: %d %v", code, got)
	}
	if code, _ := e.internal("GET", "/internal/v1/organizations/"+orgB+"/commands/"+cmd["id"].(string), internalTok, nil); code != 404 {
		t.Fatalf("wrong organization: %d", code)
	}
	bad := map[string]any{"organization_id": orgA, "application_id": "app-collector", "name": "restart"}
	if code, body := e.internal("POST", "/internal/v1/commands", internalTok, bad); code != 400 || errCode(body) != "invalid_command" {
		t.Fatalf("target without receive_commands: %d %v", code, body)
	}
	// The internal API is not on the public listener, and probes/metrics work.
	if code, _ := e.request(e.public.URL, "POST", "/internal/v1/commands", map[string]string{"X-Data-Plane-Internal-Token": internalTok}, body); code != 404 {
		t.Fatalf("internal API exposed publicly: %d", code)
	}
	if code, _ := e.request(e.public.URL, "GET", "/metrics", nil, nil); code != 404 {
		t.Fatalf("metrics exposed publicly: %d", code)
	}
	if code, ready := e.request(e.ops.URL, "GET", "/readyz", nil, nil); code != 200 || ready["ready"] != true {
		t.Fatalf("readyz: %d %v", code, ready)
	}
	resp, _ := http.Get(e.ops.URL + "/metrics")
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if !strings.Contains(string(raw), "meteorcloud_dataplane_commands_total") || strings.Contains(string(raw), tokCollector) {
		t.Fatal("metrics output")
	}
}

func TestMQTTCredentials(t *testing.T) {
	disabled := newEnv(t, nil)
	if code, body := disabled.do("POST", "/v1/mqtt/credentials", tokCollector, nil); code != 404 || errCode(body) != "mqtt_not_enabled" {
		t.Fatalf("disabled: %d %v", code, body)
	}
	secret := "0123456789abcdef0123456789abcdef"
	e := newEnv(t, func(o *Options) {
		o.MQTTJWTSecret, o.MQTTPublicURL, o.MQTTTokenTTL = secret, "mqtts://broker:8883", 10*time.Minute
	})
	code, body := e.do("POST", "/v1/mqtt/credentials", tokCollector, nil)
	if code != 200 || body["username"] != "app_cred-collector" || body["url"] != "mqtts://broker:8883" {
		t.Fatalf("%d %v", code, body)
	}
	parts := strings.Split(body["password"].(string), ".")
	if len(parts) != 3 {
		t.Fatal("password must be a JWT")
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if base64.RawURLEncoding.EncodeToString(mac.Sum(nil)) != parts[2] {
		t.Fatal("signature does not verify with the shared secret")
	}
	raw, _ := base64.RawURLEncoding.DecodeString(parts[1])
	var claims struct {
		Username string  `json:"username"`
		Exp      int64   `json:"exp"`
		ACL      bus.ACL `json:"acl"`
	}
	json.Unmarshal(raw, &claims)
	if claims.Username != "app_cred-collector" || time.Until(time.Unix(claims.Exp, 0)) > 11*time.Minute {
		t.Fatalf("claims: %+v", claims)
	}
	own := bus.TopicPrefix + orgA + "/apps/collector/devices/" + dev1
	if !claims.ACL.Allows("publish", own+"/telemetry/energy/power") {
		t.Fatal("own topic must be allowed")
	}
	for _, topic := range []string{
		bus.TopicPrefix + orgA + "/apps/collector/devices/" + dev2 + "/telemetry/energy/power",
		bus.TopicPrefix + orgB + "/apps/collector/devices/" + dev1 + "/telemetry/energy/power",
		own + "/telemetry/water/flow",
	} {
		if claims.ACL.Allows("publish", topic) {
			t.Errorf("must deny %s", topic)
		}
	}
	if len(claims.ACL.Subscribe) != 0 {
		t.Fatalf("collector has no subscribe grants: %v", claims.ACL.Subscribe)
	}
	// No grants: a token with an empty ACL, which lets the application do nothing.
	_, none := e.do("POST", "/v1/mqtt/credentials", tokNoGrants, nil)
	if len(none["publish"].([]any))+len(none["subscribe"].([]any)) != 0 {
		t.Fatalf("no grants must give an empty ACL: %v", none)
	}
	if code, _ := e.do("POST", "/v1/mqtt/credentials", "mca_wrong", nil); code != 401 {
		t.Fatal("needs a valid application token")
	}
}
