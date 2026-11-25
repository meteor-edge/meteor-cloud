package registry

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fakeRegistry(t *testing.T, realm string) (host string) {
	t.Helper()
	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	host = strings.TrimPrefix(srv.URL, "http://")
	if realm == "" {
		realm = srv.URL + "/api/v1/registry/token"
	}
	mux.HandleFunc("/v2/", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("WWW-Authenticate", `Bearer realm="`+realm+`",service="x"`)
		w.WriteHeader(401)
	})
	mux.HandleFunc("/api/v1/registry/account", func(w http.ResponseWriter, r *http.Request) {
		if _, pw, _ := r.BasicAuth(); pw != "rgt_good" {
			w.WriteHeader(401)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"host": host, "namespace": "acme", "access": "push"})
	})
	return host
}

func TestSignIn(t *testing.T) {
	host := fakeRegistry(t, "")
	a, err := SignIn(context.Background(), host, "rgt_good")
	if err != nil || a.Namespace != "acme" || a.Token != "rgt_good" {
		t.Fatalf("%+v, %v", a, err)
	}
	if _, err := SignIn(context.Background(), host, "rgt_bad"); err == nil || strings.Contains(err.Error(), "rgt_bad") {
		t.Errorf("err %v", err)
	}
	if _, err := SignIn(context.Background(), "-x/y", "rgt_good"); err == nil {
		t.Error("bad host accepted")
	}
	// The token never goes to a plain HTTP address of another machine.
	if _, err := SignIn(context.Background(), fakeRegistry(t, "http://evil.example/registry/token"), "rgt_good"); err == nil {
		t.Error("plain HTTP realm accepted")
	}
}

func TestStore(t *testing.T) {
	s := Store{Path: filepath.Join(t.TempDir(), "a", "registry.json")}
	if _, err := s.Get(""); err == nil {
		t.Error("an account from nothing")
	}
	for _, h := range []string{"one", "two", "one"} {
		if err := s.Put(Account{Host: h, Token: "t-" + h}); err != nil {
			t.Fatal(err)
		}
	}
	if st, err := os.Stat(s.Path); err != nil || st.Mode().Perm() != 0o600 {
		t.Errorf("%v %v", st, err)
	}
	if a, _ := s.Get(""); a.Host != "one" {
		t.Errorf("newest %+v", a)
	}
	var out strings.Builder
	if err := Credential(s, "get", strings.NewReader("https://two/\n"), &out); err != nil || !strings.Contains(out.String(), "t-two") {
		t.Errorf("%q %v", out.String(), err)
	}
	for _, server := range []string{"unknown", ""} {
		if err := Credential(s, "get", strings.NewReader(server), &out); err == nil || !strings.Contains(err.Error(), "credentials not found") {
			t.Errorf("%q: %v", server, err)
		}
	}
	if err := Credential(s, "erase", strings.NewReader("one"), &out); err != nil {
		t.Error(err)
	}
	if all, _ := s.List(); len(all) != 1 || all[0].Host != "two" {
		t.Errorf("%+v", all)
	}
	if Credential(s, "store", strings.NewReader("{}"), &out) == nil {
		t.Error("stored a foreign credential")
	}
}

func TestPushUsesAPrivateConfigAndLeavesTheUsersAlone(t *testing.T) {
	home := t.TempDir()
	original := `{"credsStore":"desktop","currentContext":"rootless","auths":{"other":{"auth":"eA=="}}}`
	if err := os.WriteFile(filepath.Join(home, "config.json"), []byte(original), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(home, "contexts"), 0o700); err != nil {
		t.Fatal(err)
	}
	a := Account{Host: "r.example.com", Namespace: "acme", Token: "rgt_x"}
	target, err := a.Image("team/web:1.0")
	if err != nil || target != "r.example.com/acme/team/web:1.0" {
		t.Fatal(target, err)
	}
	var calls []string
	used := ""
	d := Docker{Account: a, ConfigDir: home, Run: func(_ context.Context, args ...string) error {
		calls = append(calls, strings.Join(args, " "))
		if args[0] != "--config" {
			return nil
		}
		used = args[1]
		data, _ := os.ReadFile(filepath.Join(used, "config.json"))
		var c map[string]any
		_ = json.Unmarshal(data, &c)
		want := base64.StdEncoding.EncodeToString([]byte("meteorcli:rgt_x"))
		if c["credsStore"] != nil || c["currentContext"] != "rootless" || !strings.Contains(string(data), want) || strings.Contains(string(data), "other") {
			t.Errorf("config %s", data)
		}
		if _, err := os.Stat(filepath.Join(used, "contexts")); err != nil {
			t.Error(err)
		}
		return nil
	}}
	if err := d.Push(context.Background(), "web:dev", target); err != nil {
		t.Fatal(err)
	}
	if len(calls) != 2 || calls[0] != "tag web:dev "+target || !strings.HasSuffix(calls[1], "push "+target) || strings.Contains(strings.Join(calls, " "), "rgt_x") {
		t.Errorf("calls %q", calls)
	}
	if _, err := os.Stat(used); err == nil {
		t.Error("the private config was left behind")
	}
	if data, _ := os.ReadFile(filepath.Join(home, "config.json")); string(data) != original {
		t.Error("the user's config changed")
	}
	if _, err := os.Stat(filepath.Join(home, "contexts")); err != nil {
		t.Error("the user's contexts were removed")
	}
	if d.Push(context.Background(), "--all", target) == nil {
		t.Error("an option was accepted as an image")
	}
	for _, bad := range []string{"-x", "other.example.com/acme/web:1", "../x", "Web", "a b", ""} {
		if _, err := a.Image(bad); err == nil && bad != "other.example.com/acme/web:1" {
			t.Errorf("%q accepted", bad)
		}
	}
}
