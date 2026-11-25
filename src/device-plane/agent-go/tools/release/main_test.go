package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/update"
)

func TestGenkeyAndSign(t *testing.T) {
	var out bytes.Buffer
	if err := run([]string{"genkey"}, "", &out); err != nil {
		t.Fatal(err)
	}
	var priv, pub string
	for _, line := range strings.Split(out.String(), "\n") {
		if v, ok := strings.CutPrefix(line, "private "); ok {
			priv = v
		}
		if v, ok := strings.CutPrefix(line, "public "); ok {
			pub = v
		}
	}
	dir := t.TempDir()
	for _, name := range []string{"meteorcli-linux-arm64", "meteorcli-linux-amd64", "install-rpi.sh"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := run([]string{"sign", dir, "0.0.0-edge.7+abc"}, priv, &out); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(dir, update.ManifestName))
	sig, _ := os.ReadFile(filepath.Join(dir, update.SignatureName))
	keys, err := update.ParseKeys(pub)
	if err != nil {
		t.Fatal(err)
	}
	m, err := update.VerifyManifest(raw, sig, keys)
	if err != nil {
		t.Fatal(err)
	}
	if m.Version != "0.0.0-edge.7+abc" || len(m.Files) != 2 {
		t.Fatalf("manifest = %+v", m)
	}
	if f, err := m.Binary("meteorcli-linux-arm64"); err != nil || f.Size != int64(len("meteorcli-linux-arm64")) {
		t.Fatalf("arm64 = %+v %v", f, err)
	}

	for name, c := range map[string]struct {
		args []string
		key  string
	}{
		"no key":      {[]string{"sign", dir, "1.0.0"}, ""},
		"public key":  {[]string{"sign", dir, "1.0.0"}, pub},
		"bad version": {[]string{"sign", dir, "latest"}, priv},
		"no binaries": {[]string{"sign", t.TempDir(), "1.0.0"}, priv},
		"usage":       {[]string{"sign"}, priv},
		"unknown":     {[]string{"frob"}, priv},
		"nothing":     {nil, priv},
	} {
		if err := run(c.args, c.key, &out); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
