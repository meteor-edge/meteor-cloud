package cli

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/update"
)

func TestUpdaterWritesAFailedOutcome(t *testing.T) {
	root := t.TempDir()
	release := filepath.Join(root, "release")
	write(t, filepath.Join(release, update.ManifestName), `{"schema":1,"version":"10.0.0","files":{}}`)
	write(t, filepath.Join(root, "etc", "meteorcli", "config.json"), `{"version":1,"release_url":"file://`+release+`"}`)

	r := meteorcli(t, root, nil, "updater", "--action", "act-7", "--bin", "/usr/local/bin/meteorcli")
	if r.code != 1 || !strings.Contains(r.stderr, "not signed") {
		t.Fatalf("unsigned release = %+v", r)
	}
	b, err := os.ReadFile(filepath.Join(root, "var", "lib", "meteorcli", "state", update.OutcomeFile))
	if err != nil {
		t.Fatal(err)
	}
	var o update.Outcome
	if err := json.Unmarshal(b, &o); err != nil {
		t.Fatal(err)
	}
	if o.ActionID != "act-7" || o.Status != update.StatusFailed || o.FromVersion != "9.9.9" || !strings.Contains(o.Error, "not signed") {
		t.Fatalf("outcome = %+v", o)
	}
}

func TestUpdateNeedsTheAgent(t *testing.T) {
	r := meteorcli(t, t.TempDir(), nil, "update")
	if r.code != 1 || !strings.Contains(r.stderr, "the agent is not running") {
		t.Fatalf("update without agent = %+v", r)
	}
	if r := meteorcli(t, t.TempDir(), nil, "update", "extra"); r.code != 2 {
		t.Fatalf("update with an argument = %+v", r)
	}
}

func TestConfigReleaseURL(t *testing.T) {
	root := t.TempDir()
	if r := meteorcli(t, root, nil, "config", "--release-url", "ftp://example.com/x"); r.code != 2 {
		t.Fatalf("ftp release URL = %+v", r)
	}
	r := meteorcli(t, root, nil, "config", "--release-url", "http://192.168.0.10:8000/dist")
	if r.code != 0 || !strings.Contains(r.stdout, "Release URL:    http://192.168.0.10:8000/dist") {
		t.Fatalf("set release URL = %+v", r)
	}
	if r := meteorcli(t, root, nil, "config", "--show"); !strings.Contains(r.stdout, "Release URL:    http://192.168.0.10:8000/dist") {
		t.Fatalf("show = %+v", r)
	}
	r = meteorcli(t, root, nil, "config", "--release-url", "default")
	if r.code != 0 || !strings.Contains(r.stdout, "releases/latest/download") {
		t.Fatalf("reset release URL = %+v", r)
	}
}

func TestUpdaterWaitsForAPendingAction(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "var", "lib", "meteorcli", "state", update.PendingFile), `{"version":1,"action_id":"act-1"}`)
	r := meteorcli(t, root, nil, "updater")
	if r.code != 1 || !strings.Contains(r.stderr, "still being settled") {
		t.Fatalf("updater with a pending action = %+v", r)
	}
	if _, err := os.Stat(filepath.Join(root, "var", "lib", "meteorcli", "state", update.OutcomeFile)); err == nil {
		t.Fatal("the outcome of the pending action was replaced")
	}
}
