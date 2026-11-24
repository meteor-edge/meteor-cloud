package helper

import (
	"slices"
	"strings"
	"testing"
)

func TestUpdateStart(t *testing.T) {
	f := &fakeHost{}
	h := f.host(t.TempDir())
	out, err := call(t, h, "update.start", UpdateStartRequest{ActionID: "0f1e2d3c-aaaa-4bbb-8ccc-123456789abc"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), `"unit":"meteorcli-update.service"`) {
		t.Fatalf("reply %s", out)
	}
	if len(f.calls) != 1 || f.calls[0][0] != "systemd-run" {
		t.Fatalf("calls %v", f.calls)
	}
	args := f.calls[0]
	sep := slices.Index(args, "--")
	if sep < 0 || !slices.Equal(args[sep+1:], []string{"/usr/local/bin/meteorcli", "updater", "--action", "0f1e2d3c-aaaa-4bbb-8ccc-123456789abc"}) {
		t.Fatalf("command %v", args)
	}
	for _, want := range []string{"--unit=meteorcli-update", "--collect", "--no-block", "--property=ProtectSystem=strict", "--property=NoNewPrivileges=yes"} {
		if !slices.Contains(args, want) {
			t.Errorf("missing %s in %v", want, args)
		}
	}

	// From the command line: no action id, no --action.
	f.calls = nil
	h.UpdaterBin = "/opt/test/meteorcli"
	if _, err := call(t, h, "update.start", struct{}{}); err != nil {
		t.Fatal(err)
	}
	if got := f.calls[0][len(f.calls[0])-2:]; !slices.Equal(got, []string{"/opt/test/meteorcli", "updater"}) {
		t.Fatalf("command tail %v", got)
	}
}

func TestUpdateStartRefuses(t *testing.T) {
	for name, params := range map[string]any{
		"option as id":  UpdateStartRequest{ActionID: "--help"},
		"path as id":    UpdateStartRequest{ActionID: "a/b"},
		"long id":       UpdateStartRequest{ActionID: strings.Repeat("a", 65)},
		"unknown field": map[string]any{"action_id": "a", "url": "http://evil"},
	} {
		f := &fakeHost{}
		if _, err := call(t, f.host(t.TempDir()), "update.start", params); err == nil {
			t.Errorf("%s: accepted", name)
		}
		if len(f.calls) != 0 {
			t.Errorf("%s: ran %v", name, f.calls)
		}
	}
	f := &fakeHost{fail: "systemd-run"}
	if _, err := call(t, f.host(t.TempDir()), "update.start", UpdateStartRequest{}); err == nil {
		t.Fatal("systemd-run failure not reported")
	}
}
