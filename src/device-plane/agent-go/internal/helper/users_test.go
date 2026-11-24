package helper

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/localrpc"
)

// userHost fakes useradd, userdel, chpasswd and the user database.
type userHost struct {
	calls  [][]string
	stdin  []string
	users  map[string]UserInfo
	home   string
	failOn string
}

func newUserHost(t *testing.T) (*userHost, Host) {
	u := &userHost{users: map[string]UserInfo{"pi": {UID: 1000, GID: 1000, Home: "/home/pi"}}, home: t.TempDir()}
	run := func(_ context.Context, name string, args ...string) ([]byte, error) {
		u.calls = append(u.calls, append([]string{name}, args...))
		if name == u.failOn {
			return []byte(name + ": boom"), errors.New(name + " failed")
		}
		switch name {
		case "useradd":
			who := args[len(args)-1]
			home := filepath.Join(u.home, who)
			if err := os.MkdirAll(home, 0o755); err != nil {
				return nil, err
			}
			u.users[who] = UserInfo{UID: os.Getuid(), GID: os.Getgid(), Home: home}
		case "userdel":
			delete(u.users, args[len(args)-1])
		}
		return nil, nil
	}
	h := Host{
		Run: run,
		RunInput: func(ctx context.Context, stdin []byte, name string, args ...string) ([]byte, error) {
			u.stdin = append(u.stdin, string(stdin))
			return run(ctx, name, args...)
		},
		LookupUser: func(name string) (UserInfo, error) {
			if info, ok := u.users[name]; ok {
				return info, nil
			}
			return UserInfo{}, errNoUser
		},
		HomeDir:    func(name string) string { return filepath.Join(u.home, name) },
		SudoersDir: t.TempDir(),
	}
	return u, h
}

const goodKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGR72aezhaCVWi/bpEuEaWtIh9MnmvRaBbdD7eAyFDAK ops"

func TestSetPassword(t *testing.T) {
	u, h := newUserHost(t)
	out, err := call(t, h, "users.set_password", SetPasswordRequest{Username: "pi", Password: "s3cret-pass"})
	if err != nil || string(out) != `{"changed":true}` {
		t.Fatalf("set_password = %s, %v", out, err)
	}
	if !slices.Equal(u.stdin, []string{"pi:s3cret-pass\n"}) {
		t.Errorf("chpasswd stdin = %q", u.stdin)
	}
	for _, c := range u.calls {
		if strings.Contains(strings.Join(c, " "), "s3cret") {
			t.Errorf("password on a command line: %v", c)
		}
	}
	tests := []struct {
		req  SetPasswordRequest
		code string
	}{
		{SetPasswordRequest{Username: "root", Password: "s3cret-pass"}, localrpc.CodeBadRequest},
		{SetPasswordRequest{Username: "meteor", Password: "s3cret-pass"}, localrpc.CodeBadRequest},
		{SetPasswordRequest{Username: "-rf", Password: "s3cret-pass"}, localrpc.CodeBadRequest},
		{SetPasswordRequest{Username: "pi", Password: "short"}, localrpc.CodeBadRequest},
		{SetPasswordRequest{Username: "pi", Password: "line\nbreak!"}, localrpc.CodeBadRequest},
		{SetPasswordRequest{Username: "ghost", Password: "s3cret-pass"}, localrpc.CodeFailed},
	}
	for _, tt := range tests {
		if _, err := call(t, h, "users.set_password", tt.req); localrpc.CodeOf(err) != tt.code {
			t.Errorf("%+v: err = %v, want %s", tt.req.Username, err, tt.code)
		}
	}
}

func TestAddUser(t *testing.T) {
	u, h := newUserHost(t)
	out, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass", Sudo: true, AuthorizedKey: goodKey + "\n"})
	if err != nil || string(out) != `{"created":true}` {
		t.Fatalf("add = %s, %v", out, err)
	}
	if !slices.Equal(u.calls[0], []string{"useradd", "-m", "-s", "/bin/bash", "ops"}) {
		t.Errorf("useradd = %v", u.calls[0])
	}
	if !slices.Equal(u.stdin, []string{"ops:s3cret-pass\n"}) {
		t.Errorf("chpasswd stdin = %q", u.stdin)
	}
	rule, err := os.ReadFile(filepath.Join(h.SudoersDir, "90-meteorcli-ops"))
	if err != nil || string(rule) != "ops ALL=(ALL) NOPASSWD:ALL\n" {
		t.Errorf("sudoers = %q, %v", rule, err)
	}
	if st, _ := os.Stat(filepath.Join(h.SudoersDir, "90-meteorcli-ops")); st.Mode().Perm() != 0o440 {
		t.Errorf("sudoers mode = %v", st.Mode())
	}
	keys, err := os.ReadFile(filepath.Join(u.home, "ops", ".ssh", "authorized_keys"))
	if err != nil || string(keys) != goodKey+"\n" {
		t.Errorf("authorized_keys = %q, %v", keys, err)
	}

	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass"}); localrpc.CodeOf(err) != localrpc.CodeFailed {
		t.Errorf("existing user: %v", err)
	}
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "x", Password: "s3cret-pass", AuthorizedKey: "not a key"}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("bad key: %v", err)
	}
	if _, err := call(t, h, "users.add", map[string]any{"username": "x", "password": "s3cret-pass", "shell": "/bin/sh"}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("unknown field: %v", err)
	}
}

func TestAddUserRollsBack(t *testing.T) {
	u, h := newUserHost(t)
	u.failOn = "chpasswd"
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass", Sudo: true}); err == nil {
		t.Fatal("add must fail when chpasswd fails")
	}
	last := u.calls[len(u.calls)-1]
	if !slices.Equal(last, []string{"userdel", "-r", "ops"}) {
		t.Errorf("last call = %v, want userdel -r", last)
	}
	if _, ok := u.users["ops"]; ok {
		t.Error("the half-made user is still there")
	}
	if _, err := os.Stat(filepath.Join(h.SudoersDir, "90-meteorcli-ops")); !os.IsNotExist(err) {
		t.Error("sudoers rule left behind")
	}
}

func TestAddUserPreservesExistingHomeOnRollback(t *testing.T) {
	u, h := newUserHost(t)
	home := filepath.Join(u.home, "ops")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(home, "keep-me")
	if err := os.WriteFile(marker, []byte("data"), 0o644); err != nil {
		t.Fatal(err)
	}
	u.failOn = "chpasswd"
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass"}); err == nil {
		t.Fatal("add must fail when chpasswd fails")
	}
	last := u.calls[len(u.calls)-1]
	if !slices.Equal(last, []string{"userdel", "ops"}) {
		t.Errorf("last call = %v, want userdel without -r", last)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Errorf("pre-existing home must stay: %v", err)
	}
}

func TestAddUserLookupErrorStopsCreation(t *testing.T) {
	_, h := newUserHost(t)
	h.LookupUser = func(string) (UserInfo, error) { return UserInfo{}, errors.New("nss down") }
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass"}); err == nil {
		t.Fatal("add must fail when lookup fails unexpectedly")
	}
}

func TestAddUserDoesNotRemoveExistingSudoersOnRollback(t *testing.T) {
	u, h := newUserHost(t)
	path := filepath.Join(h.SudoersDir, "90-meteorcli-ops")
	if err := os.WriteFile(path, []byte("ops ALL=(ALL) NOPASSWD:ALL\n"), 0o440); err != nil {
		t.Fatal(err)
	}
	u.failOn = "chpasswd"
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass", Sudo: true}); err == nil {
		t.Fatal("add must fail")
	}
	if _, err := os.Stat(path); err != nil {
		t.Errorf("existing sudoers must stay: %v", err)
	}
}

func TestAddUserRollsBackAHalfMadeAccount(t *testing.T) {
	u, h := newUserHost(t)
	// useradd that creates the account and then fails (exit 12: no home).
	plain := h.Run
	h.Run = func(ctx context.Context, name string, args ...string) ([]byte, error) {
		out, _ := plain(ctx, name, args...)
		if name == "useradd" {
			return []byte("useradd: cannot create directory"), errors.New("exit status 12")
		}
		return out, nil
	}
	if _, err := call(t, h, "users.add", AddUserRequest{Username: "ops", Password: "s3cret-pass"}); err == nil {
		t.Fatal("add must fail")
	}
	if _, ok := u.users["ops"]; ok {
		t.Error("the account useradd left behind was not removed")
	}
}

func TestAuditUserNeverLogsSecrets(t *testing.T) {
	raw, _ := json.Marshal(AddUserRequest{Username: "ops", Password: "s3cret-pass", AuthorizedKey: goodKey})
	got := auditUser(raw)
	if s := jsonOf(got); strings.Contains(s, "s3cret") || strings.Contains(s, "AAAA") || !strings.Contains(s, "ops") {
		t.Errorf("audit = %v", got)
	}
}

func jsonOf(v any) string { b, _ := json.Marshal(v); return string(b) }

func TestDockerOps(t *testing.T) {
	f := &fakeHost{output: "web\ndb\n"}
	h := f.host(t.TempDir())
	out, err := call(t, h, "docker.ps", nil)
	if err != nil || string(out) != `{"output":"web\ndb\n","ok":true}` {
		t.Errorf("ps = %s, %v", out, err)
	}
	if _, err := call(t, h, "docker.inspect", DockerInspectRequest{Names: []string{"web", "db"}}); err != nil {
		t.Fatal(err)
	}
	want := []string{"docker", "inspect", "-f", InspectTemplate, "web", "db"}
	if got := f.calls[len(f.calls)-1]; !slices.Equal(got, want) {
		t.Errorf("inspect ran %v", got)
	}
	if _, err := call(t, h, "docker.stats", nil); err != nil {
		t.Fatal(err)
	}
	if got := f.calls[len(f.calls)-1]; !slices.Equal(got, []string{"docker", "stats", "--no-stream", "--format", StatsFormat}) {
		t.Errorf("stats ran %v", got)
	}
	for _, names := range [][]string{nil, {"-v"}, {"a b"}, make([]string, MaxContainers+1)} {
		if _, err := call(t, h, "docker.inspect", DockerInspectRequest{Names: names}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("names %q: err = %v", names, err)
		}
	}
	f.fail = "docker"
	if _, err := call(t, h, "docker.ps", nil); err == nil {
		t.Error("a failing docker with no output must fail")
	}
}

func TestLogsWarnings(t *testing.T) {
	f := &fakeHost{output: `{"__CURSOR":"s=1;i=2","__REALTIME_TIMESTAMP":"1767225600000000","PRIORITY":"4","_SYSTEMD_UNIT":"kernel","SYSLOG_IDENTIFIER":"kernel","MESSAGE":"Under-voltage detected"}
not json
{"MESSAGE":[104,105,255],"__CURSOR":"s=1;i=3"}
{"MESSAGE":"","__CURSOR":"s=1;i=4"}
`}
	h := f.host(t.TempDir())
	out, err := call(t, h, "logs.warnings", nil)
	if err != nil {
		t.Fatal(err)
	}
	var got WarningsReply
	_ = json.Unmarshal(out, &got)
	if len(got.Entries) != 2 {
		t.Fatalf("entries = %s", out)
	}
	e := got.Entries[0]
	if e.Message != "Under-voltage detected" || *e.Priority != 4 || *e.Unit != "kernel" || e.Cursor != "s=1;i=2" || *e.Timestamp != "2026-01-01T00:00:00.000000Z" {
		t.Errorf("entry = %s", out)
	}
	if got.Entries[1].Message != "hi�" {
		t.Errorf("byte-array message = %q", got.Entries[1].Message)
	}
	if !slices.Contains(f.calls[0], "--since=-10min") {
		t.Errorf("first look: %v", f.calls[0])
	}
	if _, err := call(t, h, "logs.warnings", WarningsRequest{AfterCursor: "s=1;i=2"}); err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(f.calls[1], "--after-cursor=s=1;i=2") {
		t.Errorf("after cursor: %v", f.calls[1])
	}
	if _, err := call(t, h, "logs.warnings", WarningsRequest{AfterCursor: "--rotate"}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("bad cursor: %v", err)
	}
}
