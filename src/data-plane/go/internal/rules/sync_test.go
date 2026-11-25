package rules

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/eval"
	"meteorcloud/dataplane/internal/rules/state"
)

const hotRule = `{"schema_version":1,"name":"Hot","scope":{"type":"organization"},"condition":{"type":"comparison","field":"temp","operator":"gt","value":80}}`

type fakeSource struct {
	snaps []*engine.Snapshot
	err   error
}

func (f *fakeSource) Fetch(_ context.Context, known string) (*engine.Snapshot, error) {
	if f.err != nil {
		return nil, f.err
	}
	if len(f.snaps) == 0 || f.snaps[0].Version == known {
		return nil, nil
	}
	s := f.snaps[0]
	f.snaps = f.snaps[1:]
	return s, nil
}

func snapshot(version string, withRule bool) *engine.Snapshot {
	s := &engine.Snapshot{Version: version, Devices: []engine.Device{{ID: "dev-1", OrganizationID: "org-1"}}}
	if withRule {
		s.Rules = []engine.RuleRecord{{ID: "r1", OrganizationID: "org-1", Version: 1, Definition: json.RawMessage(hotRule)}}
	}
	return s
}

func TestSyncerAppliesSnapshotsAndRetiresRemovedRules(t *testing.T) {
	store := state.NewMemory()
	clock := engine.NewFakeClock(time.Date(2026, 11, 13, 12, 0, 0, 0, time.UTC))
	e := engine.New(engine.Config{CommitLinger: time.Millisecond}, store, clock, nil, nil)
	defer e.Close()
	src := &fakeSource{snaps: []*engine.Snapshot{snapshot("v1", true)}}
	s := &Syncer{Engine: e, Source: src, Limits: dsl.DefaultLimits(), Log: slog.New(slog.DiscardHandler)}
	ctx := context.Background()
	if err := s.Once(ctx); err != nil || !e.Ready() || e.Rules().Version != "v1" {
		t.Fatalf("loaded: %v", err)
	}
	if err := e.Handle(ctx, &eval.Event{ID: "e1", OrgID: "org-1", DeviceID: "dev-1", Stream: dsl.DefaultStream,
		Time: clock.Now(), Fields: map[string]eval.Value{"temp": eval.Num(90)}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Once(ctx); err != nil || len(store.Outbox()) != 1 {
		t.Fatalf("unchanged snapshot keeps state: %v %d", err, len(store.Outbox()))
	}

	src.snaps = []*engine.Snapshot{snapshot("v2", false)}
	src.err = errors.New("control plane down")
	if err := s.Once(ctx); err == nil || e.Rules().Version != "v1" {
		t.Fatal("a failed fetch keeps the loaded rules")
	}
	src.err = nil
	if err := s.Once(ctx); err != nil {
		t.Fatal(err)
	}
	out := store.Outbox()
	if len(out) != 2 || out[1].Transition != "resolved" || !strings.Contains(out[1].Key, "rule_removed") {
		t.Fatalf("removing the rule resolves its alert: %+v", out)
	}
}

func TestHTTPSnapshotSourceUsesETag(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Data-Plane-Internal-Token") != "tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.Header.Get("If-None-Match") == `"v1"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		json.NewEncoder(w).Encode(snapshot("v1", true))
	}))
	defer srv.Close()
	src := &HTTPSnapshotSource{URL: srv.URL, Token: "tok"}
	s, err := src.Fetch(context.Background(), "")
	if err != nil || s.Version != "v1" || len(s.Rules) != 1 {
		t.Fatalf("fetch: %+v %v", s, err)
	}
	if s, err := src.Fetch(context.Background(), "v1"); s != nil || err != nil {
		t.Fatalf("304 is nil, nil: %v %v", s, err)
	}
	if _, err := (&HTTPSnapshotSource{URL: srv.URL, Token: "bad"}).Fetch(context.Background(), ""); err == nil {
		t.Fatal("401 is an error")
	}
}

func TestInternalAPI(t *testing.T) {
	store := state.NewMemory()
	e := engine.New(engine.Config{CommitLinger: time.Millisecond}, store, nil, nil, nil)
	defer e.Close()
	srv := httptest.NewServer((&API{Engine: e, Token: "tok", Limits: dsl.DefaultLimits()}).Handler())
	defer srv.Close()
	call := func(method, path, token, body string) (int, string) {
		req, _ := http.NewRequest(method, srv.URL+path, strings.NewReader(body))
		req.Header.Set("X-Data-Plane-Internal-Token", token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(raw)
	}
	if code, _ := call("GET", "/internal/rules/engine", "nope", ""); code != http.StatusUnauthorized {
		t.Fatal("token required")
	}
	if code, body := call("GET", "/internal/rules/engine", "tok", ""); code != 200 || !strings.Contains(body, `"ready":false`) {
		t.Fatalf("engine info %d %s", code, body)
	}
	sim := `{"definition":` + hotRule + `,"events":[{"time":"2026-11-13T12:00:00Z","data":{"temp":70}},{"time":"2026-11-13T12:00:10Z","data":{"temp":85}}]}`
	code, body := call("POST", "/internal/rules/simulate", "tok", sim)
	if code != 200 || !strings.Contains(body, `"transition":"firing"`) {
		t.Fatalf("simulate %d %s", code, body)
	}
	code, body = call("POST", "/internal/rules/simulate", "tok", `{"definition":{"schema_version":1},"events":[]}`)
	if code != http.StatusUnprocessableEntity || !strings.Contains(body, "problems") {
		t.Fatalf("invalid rule %d %s", code, body)
	}
	if code, body := call("GET", "/internal/rules/status/r1", "tok", ""); code != 200 || body != "{\"devices\":[]}\n" {
		t.Fatalf("status %d %s", code, body)
	}
}
