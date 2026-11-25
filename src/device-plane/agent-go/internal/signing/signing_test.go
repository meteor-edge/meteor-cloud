package signing

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/state"
)

// The expected bytes come from Python's json.dumps(v, sort_keys=True,
// separators=(",", ":"), ensure_ascii=False), which the server signs.
var canonicalCases = []struct{ in, want string }{
	{"{\"b\":1,\"a\":[true,false,null],\"c\":\"x\"}",
		"{\"a\":[true,false,null],\"b\":1,\"c\":\"x\"}"},
	{"{\"s\":\"quote\\\" back\\\\ nl\\n tab\\t ctl\\u0001 del\\u007f ls\\u2028 lt< amp& e\\u00e9 emoji\\ud83d\\ude00\"}",
		"{\"s\":\"quote\\\" back\\\\ nl\\n tab\\t ctl\\u0001 del\u007f ls\u2028 lt< amp& e\u00e9 emoji\U0001f600\"}"},
	{"{\"f\":[1.0,1.5,0.0001,0.00001,1e16,123456789012345678,1.5e300,-2.5,100.0,1e15,12345.678,0.1,1e-7,2.5e-5]}",
		"{\"f\":[1.0,1.5,0.0001,1e-05,1e+16,123456789012345678,1.5e+300,-2.5,100.0,1000000000000000.0,12345.678,0.1,1e-07,2.5e-05]}"},
	{"{\"\\u00e9\":1,\"z\":2,\"A\":3,\"\\ud83d\\ude00\":4,\"\\uffff\":5}",
		"{\"A\":3,\"z\":2,\"\u00e9\":1,\"\uffff\":5,\"\U0001f600\":4}"},
	{"{\"nested\":{\"env\":{\"K\":\"v\"},\"ports\":[\"8080:80\"],\"n\":-0.0,\"big\":12345678901234567890123}}",
		"{\"nested\":{\"big\":12345678901234567890123,\"env\":{\"K\":\"v\"},\"n\":-0.0,\"ports\":[\"8080:80\"]}}"},
}

func decode(t *testing.T, s string) any {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(s))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestCanonicalMatchesPython(t *testing.T) {
	for _, c := range canonicalCases {
		got, err := Canonical(decode(t, c.in))
		if err != nil {
			t.Fatalf("%s: %v", c.in, err)
		}
		if string(got) != c.want {
			t.Errorf("Canonical(%s)\n got %s\nwant %s", c.in, got, c.want)
		}
	}
}

func TestCanonicalRejectsUnknownTypes(t *testing.T) {
	if _, err := Canonical(map[string]any{"x": struct{}{}}); err == nil {
		t.Fatal("want an error for a struct")
	}
}

// org is a test organization key.
type org struct {
	id   string
	priv ed25519.PrivateKey
}

func newOrg(id string, seed byte) org {
	return org{id: id, priv: ed25519.NewKeyFromSeed(bytes.Repeat([]byte{seed}, 32))}
}

func enc(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func (o org) public() string { return enc(o.priv.Public().(ed25519.PublicKey)) }

func (o org) key() Key { return Key{KeyID: o.id, PublicKey: o.public()} }

// endorse returns o's key endorsed by by.
func (o org) endorsedBy(by org) Key {
	msg, _ := Canonical(map[string]any{"key_id": o.id, "public_key": o.public()})
	k := o.key()
	k.EndorsedBy, k.Endorsement = by.id, enc(ed25519.Sign(by.priv, append([]byte(endorsementContext), msg...)))
	return k
}

func (o org) sign(t *testing.T, device string, item map[string]any) map[string]any {
	t.Helper()
	msg, err := Canonical(DesiredPayload(device, item))
	if err != nil {
		t.Fatal(err)
	}
	item["key_id"], item["signature"] = o.id, enc(ed25519.Sign(o.priv, append([]byte(desiredContext), msg...)))
	return item
}

func item(t *testing.T) map[string]any {
	return decode(t, `{"workload_key":"app","kind":"docker","release_id":"r1","release_version":"1.0",
		"artifact_id":null,"revision":3,"spec":{"image":"nginx","env":{"A":"1"}}}`).(map[string]any)
}

func newVerifier(t *testing.T, store *state.Store, keys func() ([]Key, error)) *Verifier {
	t.Helper()
	return &Verifier{
		Store: store, DeviceID: "d-1", Fingerprint: CredentialFingerprint("tok"),
		Fetch: func(context.Context) ([]Key, error) { return keys() },
		Log:   slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

func TestVerifier(t *testing.T) {
	a, b, evil := newOrg("k-a", 1), newOrg("k-b", 2), newOrg("k-a", 9)
	ctx := context.Background()
	tests := []struct {
		name   string
		keys   []Key
		err    error
		pinned []Key // already pinned
		item   func(t *testing.T) map[string]any
		want   string // "" or a prefix of the refusal
	}{
		{name: "pinned on first use", keys: []Key{a.key()}, item: func(t *testing.T) map[string]any { return a.sign(t, "d-1", item(t)) }},
		{name: "rotated key endorsed by a pinned key", keys: []Key{a.key(), b.endorsedBy(a)}, pinned: []Key{a.key()},
			item: func(t *testing.T) map[string]any { return b.sign(t, "d-1", item(t)) }},
		{name: "new key without endorsement", keys: []Key{a.key(), b.key()}, pinned: []Key{a.key()},
			item: func(t *testing.T) map[string]any { return b.sign(t, "d-1", item(t)) }, want: "Desired software is signed by an untrusted key (k-b)"},
		{name: "same id, other key", keys: []Key{evil.key()}, pinned: []Key{a.key()},
			item: func(t *testing.T) map[string]any { return evil.sign(t, "d-1", item(t)) }, want: "Desired software signature does not verify"},
		{name: "signed for another device", keys: []Key{a.key()},
			item: func(t *testing.T) map[string]any { return a.sign(t, "d-2", item(t)) }, want: "Desired software signature does not verify"},
		{name: "tampered spec", keys: []Key{a.key()}, item: func(t *testing.T) map[string]any {
			it := a.sign(t, "d-1", item(t))
			it["spec"].(map[string]any)["image"] = "evil"
			return it
		}, want: "Desired software signature does not verify"},
		{name: "unsigned while the server signs", keys: []Key{a.key()}, item: item, want: "Desired software is not signed"},
		{name: "unsigned and the server does not sign", err: ErrNotSigning, item: item},
		{name: "unsigned, server stopped signing but keys are pinned", err: ErrNotSigning, pinned: []Key{a.key()}, item: item,
			want: "Desired software is not signed"},
		{name: "keys cannot be fetched", err: errors.New("network down"), item: item, want: "Could not check the update signature (network down); will retry."},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			store, err := state.NewStore(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			v := newVerifier(t, store, func() ([]Key, error) { return tc.keys, tc.err })
			if tc.pinned != nil {
				keys := map[string]string{}
				for _, k := range tc.pinned {
					keys[k.KeyID] = k.PublicKey
				}
				if err := v.save(keys); err != nil {
					t.Fatal(err)
				}
			}
			got, _ := v.Check(ctx, tc.item(t))
			if (tc.want == "") != (got == "") || !strings.HasPrefix(got, tc.want) {
				t.Fatalf("Check = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestPinsBelongToOneToken(t *testing.T) {
	a, b := newOrg("k-a", 1), newOrg("k-b", 2)
	store, _ := state.NewStore(t.TempDir())
	first := newVerifier(t, store, func() ([]Key, error) { return []Key{a.key()}, nil })
	if got, _ := first.Check(context.Background(), a.sign(t, "d-1", item(t))); got != "" {
		t.Fatal(got)
	}
	// Re-enrolled into another organization: the old pins must not count.
	second := newVerifier(t, store, func() ([]Key, error) { return []Key{b.key()}, nil })
	second.Fingerprint = CredentialFingerprint("new-token")
	if got, _ := second.Check(context.Background(), b.sign(t, "d-1", item(t))); got != "" {
		t.Fatalf("new token, new org: %q", got)
	}
	if got, _ := second.Check(context.Background(), a.sign(t, "d-1", item(t))); !strings.Contains(got, "untrusted key") {
		t.Fatalf("old org key after re-enrollment: %q", got)
	}
}

func TestLatePinSaveFromTheOldTokenIsDropped(t *testing.T) {
	a, b := newOrg("k-a", 1), newOrg("k-b", 2)
	store, _ := state.NewStore(t.TempDir())
	current := CredentialFingerprint("tok")
	hold := func(save func(string) error) error { return save(current) }
	// The new enrollment pinned its organization's key.
	fresh := newVerifier(t, store, func() ([]Key, error) { return []Key{b.key()}, nil })
	fresh.Fingerprint, fresh.Hold = CredentialFingerprint("new-token"), hold
	// The old cycle's key request is still running when the device re-enrolls.
	old := newVerifier(t, store, func() ([]Key, error) {
		current = CredentialFingerprint("new-token")
		if got, _ := fresh.Check(context.Background(), b.sign(t, "d-1", item(t))); got != "" {
			t.Fatalf("new enrollment: %q", got)
		}
		return []Key{a.key()}, nil
	})
	old.Hold = hold
	_, _ = old.Check(context.Background(), a.sign(t, "d-1", item(t)))
	var p pins
	if _, err := store.Read(PinsFile, &p); err != nil {
		t.Fatal(err)
	}
	if p.CredentialFP != CredentialFingerprint("new-token") || p.Keys["k-b"] == "" || p.Keys["k-a"] != "" {
		t.Fatalf("the old cycle overwrote the new pins: %+v", p)
	}
}

func TestPythonPinsFileIsRead(t *testing.T) {
	a := newOrg("k-a", 1)
	dir := t.TempDir()
	store, _ := state.NewStore(dir)
	// The Python agent's file has no version field.
	py := `{"keys":{"k-a":"` + a.public() + `"},"device_id":"d-1","credential_fp":"` + CredentialFingerprint("tok") + `"}`
	if err := writeFile(dir+"/"+PinsFile, py); err != nil {
		t.Fatal(err)
	}
	v := newVerifier(t, store, func() ([]Key, error) { return nil, errors.New("must not fetch") })
	if got, _ := v.Check(context.Background(), a.sign(t, "d-1", item(t))); got != "" {
		t.Fatal(got)
	}
}

func writeFile(path, content string) error { return os.WriteFile(path, []byte(content), 0o600) }

func TestCheckReturnsStopErrors(t *testing.T) {
	store, err := state.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	refused := errors.New("token refused")
	v := newVerifier(t, store, func() ([]Key, error) { return nil, refused })
	v.Stop = func(err error) bool { return errors.Is(err, refused) }
	if why, err := v.Check(context.Background(), item(t)); why != "" || !errors.Is(err, refused) {
		t.Fatalf("Check = %q, %v", why, err)
	}
}
