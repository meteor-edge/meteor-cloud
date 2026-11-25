package registry

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
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

func TestStoreKeepsWhatItDoesNotKnow(t *testing.T) {
	s := Store{Path: filepath.Join(t.TempDir(), "registry.json")}
	old := `{"version":7,"later":true,"accounts":[{"host":"one","token":"t","new":"x"},{"host":"two","token":"u"}]}`
	if err := os.WriteFile(s.Path, []byte(old), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := s.Put(Account{Host: "two", Token: "v"}); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(s.Path)
	var file map[string]any
	if err := json.Unmarshal(data, &file); err != nil {
		t.Fatal(err)
	}
	accounts, _ := file["accounts"].([]any)
	if file["version"] != 7.0 || file["later"] != true || len(accounts) != 2 || accounts[1].(map[string]any)["new"] != "x" {
		t.Errorf("%s", data)
	}
	if a, err := s.Get(""); err != nil || a.Host != "two" || a.Token != "v" {
		t.Errorf("%+v %v", a, err)
	}
}

func TestDamagedStoreIsMovedAside(t *testing.T) {
	for _, damaged := range []string{`{"accounts":[`, `null`, `[]`, `{"accounts":[null]}`, `{"accounts":[{"host":3}]}`} {
		s := Store{Path: filepath.Join(t.TempDir(), "registry.json")}
		if err := os.WriteFile(s.Path, []byte(damaged), 0o600); err != nil {
			t.Fatal(err)
		}
		if all, err := s.List(); err != nil || len(all) != 0 {
			t.Errorf("%s: %+v %v", damaged, all, err)
		}
		if moved, _ := filepath.Glob(s.Path + ".corrupt-*"); len(moved) != 1 {
			t.Errorf("%s: not kept", damaged)
		}
		if err := s.Put(Account{Host: "one"}); err != nil {
			t.Error(err)
		}
	}
}

func TestStoreChangesDoNotOverwriteEachOther(t *testing.T) {
	path := filepath.Join(t.TempDir(), "registry.json")
	var wg sync.WaitGroup
	for i := range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			host := "h" + strconv.Itoa(i)
			if err := (Store{Path: path}).Put(Account{Host: host}); err != nil {
				t.Error(err)
			}
			if i%2 == 0 {
				if err := (Store{Path: path}).Remove(host); err != nil {
					t.Error(err)
				}
			}
		}()
	}
	wg.Wait()
	if all, err := (Store{Path: path}).List(); err != nil || len(all) != 10 {
		t.Errorf("%d accounts, %v", len(all), err)
	}
}

func TestDockerSettingsThatAreNull(t *testing.T) {
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "config.json"), []byte("null"), 0o600); err != nil {
		t.Fatal(err)
	}
	d := Docker{Account: Account{Host: "r", Token: "t"}, ConfigDir: home, Run: func(context.Context, ...string) error { return nil }}
	if err := d.Pull(context.Background(), "r/acme/web:1"); err != nil {
		t.Error(err)
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
	if full, err := a.Image(target); err != nil || full != target {
		t.Errorf("%q, %v", full, err)
	}
	for _, bad := range []string{
		"-x", "other.example.com/acme/web:1", "r.example.com/other/web:1", "r.example.com/acme", "localhost/web",
		"localhost:5000/acme/web:1", "../x", "Web", "a b", "",
	} {
		if _, err := a.Image(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}
