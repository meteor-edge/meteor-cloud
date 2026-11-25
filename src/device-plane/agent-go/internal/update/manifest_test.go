package update

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// testKey is a fixed key pair for tests.
func testKey(t *testing.T) (ed25519.PublicKey, ed25519.PrivateKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return pub, priv
}

// signed returns a manifest for the binaries and its signature.
func signed(t *testing.T, priv ed25519.PrivateKey, version string, bins map[string][]byte) (raw, sig []byte) {
	t.Helper()
	m := Manifest{Schema: ManifestSchema, Version: version, Files: map[string]File{}}
	for name, b := range bins {
		s := sha256.Sum256(b)
		m.Files[name] = File{SHA256: hex.EncodeToString(s[:]), Size: int64(len(b))}
	}
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return raw, []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, raw)) + "\n")
}

func TestCompiledKeyParses(t *testing.T) {
	keys, err := ParseKeys(ReleaseKeys())
	if err != nil || len(keys) == 0 {
		t.Fatalf("compiled-in release key: %v", err)
	}
}

func TestParseKeys(t *testing.T) {
	pub, _ := testKey(t)
	pub2, _ := testKey(t)
	b := base64.StdEncoding.EncodeToString
	if keys, err := ParseKeys(b(pub) + ", " + b(pub2)); err != nil || len(keys) != 2 {
		t.Fatalf("two keys: %v %d", err, len(keys))
	}
	for _, bad := range []string{"", " , ", "not base64!", b([]byte("short"))} {
		if _, err := ParseKeys(bad); err == nil {
			t.Errorf("ParseKeys(%q) accepted", bad)
		}
	}
}

func TestVerifyManifest(t *testing.T) {
	pub, priv := testKey(t)
	other, otherPriv := testKey(t)
	raw, sig := signed(t, priv, "0.2.0", map[string][]byte{"meteorcli-linux-arm64": []byte("bin")})

	m, err := VerifyManifest(raw, sig, []ed25519.PublicKey{other, pub})
	if err != nil || m.Version != "0.2.0" {
		t.Fatalf("good manifest: %v %+v", err, m)
	}
	if f, err := m.Binary("meteorcli-linux-arm64"); err != nil || f.Size != 3 {
		t.Fatalf("Binary: %v %+v", err, f)
	}
	if _, err := m.Binary("meteorcli-linux-amd64"); err == nil {
		t.Fatal("missing binary accepted")
	}

	tampered := []byte(strings.Replace(string(raw), "0.2.0", "9.9.9", 1))
	_, otherSig := signed(t, otherPriv, "0.2.0", map[string][]byte{"meteorcli-linux-arm64": []byte("bin")})
	cases := map[string]struct{ raw, sig []byte }{
		"tampered":      {tampered, sig},
		"no signature":  {raw, nil},
		"garbage sig":   {raw, []byte("!!!")},
		"wrong key":     {raw, otherSig},
		"short sig":     {raw, []byte(base64.StdEncoding.EncodeToString([]byte("abc")))},
		"empty":         {nil, sig},
		"sig of other ": {raw, []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte("x"))))},
	}
	for name, c := range cases {
		if _, err := VerifyManifest(c.raw, c.sig, []ed25519.PublicKey{pub}); !errors.Is(err, ErrUnsigned) {
			t.Errorf("%s: err = %v, want ErrUnsigned", name, err)
		}
	}
}

func TestVerifyManifestChecksShape(t *testing.T) {
	pub, priv := testKey(t)
	sign := func(raw string) []byte {
		return []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte(raw))))
	}
	for name, raw := range map[string]string{
		"schema":  `{"schema":2,"version":"0.2.0","files":{}}`,
		"version": `{"schema":1,"version":"latest","files":{}}`,
		"json":    `{"schema":1,`,
	} {
		if _, err := VerifyManifest([]byte(raw), sign(raw), []ed25519.PublicKey{pub}); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	raw := `{"schema":1,"version":"0.2.0","files":{"a":{"sha256":"zz","size":3},"b":{"sha256":"` + strings.Repeat("ab", 32) + `","size":0}}}`
	m, err := VerifyManifest([]byte(raw), sign(raw), []ed25519.PublicKey{pub})
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"a", "b"} {
		if _, err := m.Binary(name); err == nil {
			t.Errorf("Binary(%s) accepted a bad entry", name)
		}
	}
}

func TestAssetName(t *testing.T) {
	name, err := AssetName()
	if err != nil || !strings.HasPrefix(name, "meteorcli-linux-") {
		t.Fatalf("AssetName = %q, %v", name, err)
	}
}
