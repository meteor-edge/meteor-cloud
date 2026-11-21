package update

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

const asset = "meteorcli-linux-test"

// fakeBinary is a stand-in binary: "version=<v> mode=<good|crash|silent|loop|stops|
// early|rejected|flaky>".
func fakeBinary(version, mode string) []byte {
	return []byte("version=" + version + " mode=" + mode + "\n")
}

func field(b []byte, key string) string {
	for _, f := range strings.Fields(string(b)) {
		if v, ok := strings.CutPrefix(f, key+"="); ok {
			return v
		}
	}
	return ""
}

// fakeSystem runs the fake binary at bin when the agent unit is restarted.
type fakeSystem struct {
	mu         sync.Mutex
	bin        string
	running    []byte
	restarts   int
	invocation int
	probes     int
	calls      []string
	enrolled   bool
}

func (s *fakeSystem) Restart(_ context.Context, unit string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, "restart "+unit)
	b, err := os.ReadFile(s.bin)
	if err != nil {
		return err
	}
	s.running = b
	// A manual restart is a new activation and resets NRestarts, as systemd does.
	s.invocation++
	s.restarts = 0
	if field(b, "mode") == "crash" {
		return errors.New("Job for meteorcli.service failed")
	}
	return nil
}

func (s *fakeSystem) Stop(_ context.Context, unit string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, "stop "+unit)
	// "early" crashes while the helper stops and systemd restarts it at once.
	if field(s.running, "mode") == "early" {
		s.invocation++
		s.restarts++
	}
	return nil
}

func (s *fakeSystem) Unit(context.Context, string) (UnitState, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if field(s.running, "mode") == "loop" {
		s.restarts++
		s.invocation++
	}
	return UnitState{Active: field(s.running, "mode") != "stops", Restarts: s.restarts, Invocation: fmt.Sprint(s.invocation)}, nil
}

func (s *fakeSystem) probe(context.Context) (AgentStatus, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.running == nil {
		return AgentStatus{}, errors.New("not running")
	}
	s.probes++
	mode := field(s.running, "mode")
	// "flaky" answers once, then never again for long enough to settle.
	if mode == "flaky" && s.probes > 1 && s.probes%3 != 0 {
		return AgentStatus{}, errors.New("no answer")
	}
	st := AgentStatus{Version: field(s.running, "version"), State: "waiting_for_enrollment"}
	if mode == "rejected" {
		return AgentStatus{Version: st.Version, State: rejectedState}, nil
	}
	if s.enrolled {
		st.State = enrolledState
		if field(s.running, "mode") != "silent" {
			now := time.Now()
			st.LastHeartbeat = &now
		}
	}
	return st, nil
}

func fakeSmoke(_ context.Context, path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return field(b, "version"), nil
}

type fixture struct {
	u       *Updater
	sys     *fakeSystem
	release string
	priv    ed25519.PrivateKey
}

// newFixture installs 0.1.0 and publishes nothing yet.
func newFixture(t *testing.T) *fixture {
	t.Helper()
	dir := t.TempDir()
	bin := filepath.Join(dir, "bin", "meteorcli")
	release := filepath.Join(dir, "release")
	for _, d := range []string{filepath.Dir(bin), release} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(bin, fakeBinary("0.1.0", "good"), 0o755); err != nil {
		t.Fatal(err)
	}
	pub, priv := testKey(t)
	sys := &fakeSystem{bin: bin, running: fakeBinary("0.1.0", "good"), enrolled: true}
	fetch, err := NewHTTPFetcher("file://"+release, "meteorcli/test", nil)
	if err != nil {
		t.Fatal(err)
	}
	u := &Updater{
		Fetch: fetch, Keys: []ed25519.PublicKey{pub}, Asset: asset, Bin: bin, Current: "0.1.0",
		System: sys, Probe: sys.probe, Smoke: fakeSmoke,
		HealthTimeout: 2 * time.Second, Poll: 10 * time.Millisecond, Settle: 50 * time.Millisecond,
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	return &fixture{u: u, sys: sys, release: release, priv: priv}
}

// publish writes a signed release with one binary.
func (f *fixture) publish(t *testing.T, version string, bin []byte) {
	t.Helper()
	raw, sig := signed(t, f.priv, version, map[string][]byte{asset: bin})
	f.write(t, map[string][]byte{asset: bin, ManifestName: raw, SignatureName: sig})
}

func (f *fixture) write(t *testing.T, files map[string][]byte) {
	t.Helper()
	for name, b := range files {
		if err := os.WriteFile(filepath.Join(f.release, name), b, 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func (f *fixture) installed(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(f.u.Bin)
	if err != nil {
		t.Fatal(err)
	}
	return field(b, "version")
}

func TestUpdateToAGoodBuild(t *testing.T) {
	f := newFixture(t)
	f.publish(t, "0.2.0", fakeBinary("0.2.0", "good"))
	out := f.u.Run(context.Background())
	if out.Status != StatusUpdated || out.FromVersion != "0.1.0" || out.ToVersion != "0.2.0" || out.Error != "" {
		t.Fatalf("outcome = %+v", out)
	}
	if v := f.installed(t); v != "0.2.0" {
		t.Fatalf("installed %s", v)
	}
	prev, err := os.ReadFile(f.u.Previous())
	if err != nil || field(prev, "version") != "0.1.0" {
		t.Fatalf("previous = %q, %v", prev, err)
	}
	if want := []string{"restart meteorcli.service", "stop meteorcli-helper.service"}; strings.Join(f.sys.calls, ",") != strings.Join(want, ",") {
		t.Fatalf("calls = %v", f.sys.calls)
	}
	if _, err := os.Stat(f.u.newPath()); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("staged file left behind")
	}
}

func TestUpdateNotEnrolledSettles(t *testing.T) {
	f := newFixture(t)
	f.sys.enrolled = false
	f.publish(t, "0.2.0", fakeBinary("0.2.0", "good"))
	if out := f.u.Run(context.Background()); out.Status != StatusUpdated {
		t.Fatalf("outcome = %+v", out)
	}
}

func TestUpdateNotEnrolledNeedsAnUnbrokenSettle(t *testing.T) {
	f := newFixture(t)
	f.sys.enrolled = false
	f.u.HealthTimeout = 500 * time.Millisecond
	f.publish(t, "0.2.0", fakeBinary("0.2.0", "flaky"))
	// Answers every third probe only: never 50 ms without a break.
	f.u.Settle = 25 * time.Millisecond
	out := f.u.Run(context.Background())
	if out.Status != StatusRolledBack {
		t.Fatalf("outcome = %+v (gaps in the answers counted as healthy)", out)
	}
}

func TestParseShow(t *testing.T) {
	st, err := parseShow("ActiveState=active\nNRestarts=2\nInvocationID=abc\n")
	if err != nil || !st.Active || st.Restarts != 2 || st.Invocation != "abc" {
		t.Fatalf("parseShow = %+v, %v", st, err)
	}
	if st, _ := parseShow("ActiveState=activating\nNRestarts=0\n"); st.Active {
		t.Fatal("activating counted as active")
	}
	if _, err := parseShow("NRestarts=x"); err == nil {
		t.Fatal("bad NRestarts accepted")
	}
}

func TestUpdateRollsBack(t *testing.T) {
	cases := map[string]string{
		"exits at start":                 "crash",
		"crash loop":                     "loop",
		"crashes while the helper stops": "early",
		"stops":                          "stops",
		"never heartbeats":               "silent",
		"heartbeat refused":              "rejected",
	}
	for name, mode := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFixture(t)
			f.publish(t, "0.2.0", fakeBinary("0.2.0", mode))
			out := f.u.Run(context.Background())
			if out.Status != StatusRolledBack || !strings.Contains(out.Error, "rolled back to 0.1.0") {
				t.Fatalf("outcome = %+v", out)
			}
			if v := f.installed(t); v != "0.1.0" {
				t.Fatalf("installed %s after rollback", v)
			}
			if v := field(f.sys.running, "version"); v != "0.1.0" {
				t.Fatalf("running %s after rollback", v)
			}
			if _, err := os.Stat(f.u.Previous()); err != nil {
				t.Fatalf("previous removed by rollback: %v", err)
			}
		})
	}
}

func TestUpdateRefusals(t *testing.T) {
	good := fakeBinary("0.2.0", "good")
	cases := map[string]struct {
		setup  func(t *testing.T, f *fixture)
		status string
		want   string
	}{
		"unsigned": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.2.0", good)
			os.Remove(filepath.Join(f.release, SignatureName))
		}, StatusFailed, "not signed"},
		"other key": {func(t *testing.T, f *fixture) {
			_, f.priv = testKey(t)
			f.publish(t, "0.2.0", good)
		}, StatusFailed, "not signed"},
		"swapped binary": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.2.0", good)
			f.write(t, map[string][]byte{asset: fakeBinary("0.2.0", "evil-and-longer")})
		}, StatusFailed, "bytes"},
		"same size, other bytes": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.2.0", good)
			f.write(t, map[string][]byte{asset: fakeBinary("0.2.1", "good")})
		}, StatusFailed, "checksum mismatch"},
		"binary lies about its version": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.2.0", fakeBinary("0.3.0", "good"))
		}, StatusFailed, "reports"},
		"no binary for this CPU": {func(t *testing.T, f *fixture) {
			raw, sig := signed(t, f.priv, "0.2.0", map[string][]byte{"meteorcli-linux-other": good})
			f.write(t, map[string][]byte{ManifestName: raw, SignatureName: sig})
		}, StatusFailed, "has no " + asset},
		"no release": {func(*testing.T, *fixture) {}, StatusFailed, "no such file"},
		"same version": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.1.0+other", good)
		}, StatusAlreadyCurrent, ""},
		"older release": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.0.9", good)
		}, StatusAlreadyCurrent, ""},
		"disk full": {func(t *testing.T, f *fixture) {
			f.publish(t, "0.2.0", good)
			f.u.FreeBytes = func(string) (uint64, error) { return 1 << 20, nil }
		}, StatusFailed, "not enough free space"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFixture(t)
			c.setup(t, f)
			out := f.u.Run(context.Background())
			if out.Status != c.status || !strings.Contains(out.Error, c.want) {
				t.Fatalf("outcome = %+v, want %s with %q", out, c.status, c.want)
			}
			if v := f.installed(t); v != "0.1.0" {
				t.Fatalf("installed %s", v)
			}
			if len(f.sys.calls) != 0 {
				t.Fatalf("touched systemd: %v", f.sys.calls)
			}
			if _, err := os.Stat(f.u.newPath()); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("staged file left behind")
			}
		})
	}
}

func TestHTTPFetcher(t *testing.T) {
	pub, priv := testKey(t)
	bin := fakeBinary("0.2.0", "good")
	raw, sig := signed(t, priv, "0.2.0", map[string][]byte{asset: bin})
	files := map[string][]byte{ManifestName: raw, SignatureName: sig, asset: bin}
	var agents []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		agents = append(agents, r.Header.Get("User-Agent")+" auth="+r.Header.Get("Authorization"))
		if r.URL.Path == "/latest/"+asset {
			http.Redirect(w, r, "/storage/"+asset, http.StatusFound)
			return
		}
		b, ok := files[filepath.Base(r.URL.Path)]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(b)
	}))
	defer srv.Close()
	f, err := NewHTTPFetcher(srv.URL+"/latest/", "meteorcli/0.1.0", nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	m, err := VerifyManifest(must(f.Get(ctx, ManifestName, maxManifest)), must(f.Get(ctx, SignatureName, maxSignature)), []ed25519.PublicKey{pub})
	if err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(t.TempDir(), "bin")
	if err := f.Download(ctx, asset, m.Files[asset], dest); err != nil {
		t.Fatalf("download through a redirect: %v", err)
	}
	if st, err := os.Stat(dest); err != nil || st.Mode().Perm() != 0o755 {
		t.Fatalf("downloaded file: %v %v", st, err)
	}
	for _, a := range agents {
		if a != "meteorcli/0.1.0 auth=" {
			t.Fatalf("request headers %q", a)
		}
	}
	if _, err := f.Get(ctx, ManifestName, 3); err == nil {
		t.Fatal("oversized file accepted")
	}
	if _, err := f.Get(ctx, "missing", 100); err == nil || !strings.Contains(err.Error(), "HTTP 404") {
		t.Fatalf("missing file: %v", err)
	}
	for _, bad := range []string{"ftp://x/y", "https://", "relative/path", "", "https://u:secret@x/dist",
		"https://x/dist?a=b", "https://x/dist#edge", "https://x/dist?"} {
		_, err := NewHTTPFetcher(bad, "", nil)
		if err == nil {
			t.Errorf("release URL %q accepted", bad)
		} else if strings.Contains(err.Error(), "secret") {
			t.Errorf("error repeats the URL: %v", err)
		}
	}
	// The file name is a path element (escaped), not glued to the string.
	var paths []string
	srv2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.EscapedPath())
		w.Write([]byte("x"))
	}))
	defer srv2.Close()
	f2, _ := NewHTTPFetcher(srv2.URL+"/a b/", "", nil)
	if _, err := f2.Get(ctx, ManifestName, 10); err != nil || len(paths) != 1 || paths[0] != "/a%20b/"+ManifestName {
		t.Fatalf("request path %v, %v", paths, err)
	}
}

func TestRedirectToHTTPIsRefused(t *testing.T) {
	via := []*http.Request{{URL: mustURL(t, "https://example.com/a")}}
	if err := checkRedirect(&http.Request{URL: mustURL(t, "http://example.com/b")}, via); err == nil {
		t.Fatal("https to http redirect allowed")
	}
	if err := checkRedirect(&http.Request{URL: mustURL(t, "https://cdn.example.com/b")}, via); err != nil {
		t.Fatalf("https redirect refused: %v", err)
	}
	// http mirror -> https -> http: the last hop is the downgrade.
	hops := []*http.Request{{URL: mustURL(t, "http://mirror/a")}, {URL: mustURL(t, "https://cdn/b")}}
	if err := checkRedirect(&http.Request{URL: mustURL(t, "http://evil/c")}, hops); err == nil {
		t.Fatal("downgrade on a later hop allowed")
	}
	if err := checkRedirect(&http.Request{URL: mustURL(t, "https://u:p@cdn/b")}, via); err == nil {
		t.Fatal("redirect with credentials allowed")
	}
	if err := checkRedirect(&http.Request{URL: mustURL(t, "http://mirror2/b")}, hops[:1]); err != nil {
		t.Fatalf("http to http refused: %v", err)
	}
}

func TestLockAndOutcome(t *testing.T) {
	dir := t.TempDir()
	unlock, err := Lock(filepath.Join(dir, "run", "lock"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Lock(filepath.Join(dir, "run", "lock")); !errors.Is(err, ErrBusy) {
		t.Fatalf("second lock: %v", err)
	}
	unlock()
	if u2, err := Lock(filepath.Join(dir, "run", "lock")); err != nil {
		t.Fatalf("lock after unlock: %v", err)
	} else {
		u2()
	}
	o := Outcome{ActionID: "a1", Status: StatusUpdated, FromVersion: "0.1.0", ToVersion: "0.2.0", FinishedAt: time.Now().UTC()}
	if err := WriteOutcome(dir, o, config.Owner{}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(dir, OutcomeFile))
	if !strings.Contains(string(b), `"version": 1`) && !strings.Contains(string(b), `"version":1`) {
		t.Fatalf("outcome file has no version: %s", b)
	}
	if !o.Done() || (Outcome{Status: StatusRolledBack}).Done() {
		t.Fatal("Done")
	}
}

func TestSmokeVersion(t *testing.T) {
	dir := t.TempDir()
	script := filepath.Join(dir, "m")
	os.WriteFile(script, []byte("#!/bin/sh\necho 'meteorcli 0.2.0'\n"), 0o755)
	if v, err := SmokeVersion(context.Background(), script); err != nil || v != "0.2.0" {
		t.Fatalf("SmokeVersion = %q, %v", v, err)
	}
	os.WriteFile(script, []byte("#!/bin/sh\necho hello\n"), 0o755)
	if _, err := SmokeVersion(context.Background(), script); err == nil {
		t.Fatal("non-meteorcli output accepted")
	}
	// A child that keeps stdout open does not hold the check past WaitDelay.
	os.WriteFile(script, []byte("#!/bin/sh\necho 'meteorcli 0.2.0'\nsleep 30 &\n"), 0o755)
	start := time.Now()
	if v, err := SmokeVersion(context.Background(), script); time.Since(start) > 10*time.Second {
		t.Fatalf("smoke check waited %v (%q, %v)", time.Since(start), v, err)
	}
	if free, err := FreeBytes(dir); err != nil || free == 0 {
		t.Fatalf("FreeBytes = %d, %v", free, err)
	}
}

func must(b []byte, err error) []byte {
	if err != nil {
		panic(err)
	}
	return b
}

func mustURL(t *testing.T, s string) *url.URL {
	t.Helper()
	u, err := url.Parse(s)
	if err != nil {
		t.Fatal(err)
	}
	return u
}
