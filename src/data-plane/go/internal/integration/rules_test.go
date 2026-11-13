package integration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/natsjs"
	"meteorcloud/dataplane/internal/natstest"
	"meteorcloud/dataplane/internal/ruleproc"
	"meteorcloud/dataplane/internal/rules"
	"meteorcloud/dataplane/internal/rules/dispatch"
	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

// fakeControlPlane serves the rule snapshot and records alert transitions.
type fakeControlPlane struct {
	mu          sync.Mutex
	snapshot    engine.Snapshot
	transitions []map[string]any
}

func (f *fakeControlPlane) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-Data-Plane-Internal-Token") != "tok" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	switch r.URL.Path {
	case "/internal/rules/snapshot":
		json.NewEncoder(w).Encode(f.snapshot)
	case "/internal/rules/alert-transitions":
		var body struct{ Items []map[string]any }
		json.NewDecoder(r.Body).Decode(&body)
		f.transitions = append(f.transitions, body.Items...)
		w.Write([]byte(`{"results":[{"status":"applied"}]}`))
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func (f *fakeControlPlane) seen() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, t := range f.transitions {
		out = append(out, fmt.Sprintf("%s/%s/%s", t["organization_id"], t["device_id"], t["event"].(map[string]any)["transition"]))
	}
	return out
}

// The whole path: MQTT message -> ingestion -> JetStream -> rule processor -> engine
// -> outbox -> dispatcher -> control plane alert record. Tenant isolation holds end to end.
func TestRuleEngineEndToEnd(t *testing.T) {
	srv := natstest.Start(t)
	_, js := srv.Connect(t)
	reg := prometheus.NewRegistry()
	nm := natsjs.NewMetrics(reg)
	ensureStream(t, js, streamConfig())
	in, _ := newIngester(js, nm, reg)
	cons := ensureConsumer(t, js, "rule-processor")

	cp := &fakeControlPlane{snapshot: engine.Snapshot{Version: "v1",
		Rules: []engine.RuleRecord{{ID: "rule-1", OrganizationID: orgA, Version: 1, Definition: json.RawMessage(
			`{"schema_version":1,"name":"High CPU","severity":"critical","scope":{"type":"organization"},
			  "condition":{"type":"comparison","field":"cpu_percent","operator":"gt","value":90}}`)}},
		Devices: []engine.Device{{ID: devA, OrganizationID: orgA}, {ID: devB, OrganizationID: orgB}}}}
	api := httptest.NewServer(cp)
	defer api.Close()

	store := state.NewMemory()
	eng := engine.New(engine.Config{CommitLinger: time.Millisecond}, store, nil, engine.NewMetrics(reg), quiet)
	defer eng.Close()
	handler := ruleproc.HandlerFunc(func(ctx context.Context, ev *envelope.Event) error {
		err := (&rules.Processor{Engine: eng}).Handle(ctx, ev)
		if errors.Is(err, engine.ErrPermanent) {
			return ruleproc.Permanent(err)
		}
		return err
	})
	stop := runProcessor(t, cons, handler, nm)
	defer stop()

	// Events that arrive before the first snapshot are redelivered, not lost.
	ingest(t, in, devA, "metrics", metrics(95))
	syncer := &rules.Syncer{Engine: eng, Source: &rules.HTTPSnapshotSource{URL: api.URL, Token: "tok"},
		Limits: dsl.DefaultLimits(), Log: quiet}
	if err := syncer.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	cpc := &dispatch.ControlPlane{URL: api.URL, Token: "tok"}
	d := dispatch.New(dispatch.Config{Every: 20 * time.Millisecond}, store,
		map[string]dispatch.Sender{dsl.ActionAlert: &dispatch.AlertSender{CP: cpc}}, nil, dispatch.NewMetrics(reg), quiet)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go d.Run(ctx)

	ingest(t, in, devB, "metrics", metrics(99)) // another organization's device: the rule never sees it
	eventually(t, "firing recorded", func() bool { return len(cp.seen()) == 1 })
	ingest(t, in, devA, "metrics", metrics(40))
	eventually(t, "resolve recorded", func() bool { return len(cp.seen()) == 2 })
	got := cp.seen()
	if got[0] != orgA+"/"+devA+"/firing" || got[1] != orgA+"/"+devA+"/resolved" {
		t.Fatalf("transitions %v", got)
	}
	if info(t, cons).NumAckPending != 0 {
		t.Fatal("every event acknowledged")
	}
}
