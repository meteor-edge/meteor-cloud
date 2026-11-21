package signing

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"sort"
	"strings"

	"meteorcloud/meteorcli/internal/state"
)

// Signature contexts; they must match app/signing on the control plane.
const (
	desiredContext     = "meteorcloud-desired-v1\n"
	endorsementContext = "meteorcloud-key-endorsement-v1\n"
)

// PinsFile is the state file with the pinned organization keys (the Python
// agent's signing-keys.json, imported unchanged).
const PinsFile = "signing-keys.json"

const pinsVersion = 1

// ErrNotSigning is what Fetch returns when the server does not sign (404, an
// older control plane): unsigned items are then accepted until keys exist.
var ErrNotSigning = errors.New("the server does not sign desired software")

// Key is one organization public key from GET /signing-keys.
type Key struct {
	KeyID       string
	PublicKey   string // raw 32 bytes, base64url
	EndorsedBy  string
	Endorsement string
}

// pins is the state file. CredentialFP ties it to the device token that pinned
// the keys, so pins of an earlier enrollment are never used.
type pins struct {
	Keys         map[string]string `json:"keys"`
	DeviceID     string            `json:"device_id"`
	CredentialFP string            `json:"credential_fp"`
}

// Verifier checks desired items for one reconcile cycle. Make a new one per
// cycle: it remembers within the cycle whether the server signs.
type Verifier struct {
	// Store keeps PinsFile.
	Store *state.Store
	// DeviceID is part of every signed payload.
	DeviceID string
	// Fingerprint is CredentialFingerprint of the current device token.
	Fingerprint string
	// Fetch returns the server's keys, or ErrNotSigning.
	Fetch func(ctx context.Context) ([]Key, error)
	// Log should carry module=deploy.
	Log *slog.Logger

	serverSigns *bool
}

// CredentialFingerprint is a short hash of the device token; pins saved under
// another token are ignored.
func CredentialFingerprint(deviceToken string) string {
	sum := sha256.Sum256([]byte(deviceToken))
	return hex.EncodeToString(sum[:])[:32]
}

// Check returns "" when item may be installed, otherwise why it is refused.
// item is the desired item as decoded JSON (UseNumber).
func (v *Verifier) Check(ctx context.Context, item map[string]any) string {
	reason, err := v.check(ctx, item)
	if err != nil {
		v.Log.Warn("could not check the signature", "workload", item["workload_key"], "err", err)
		return fmt.Sprintf("Could not check the update signature (%v); will retry.", err)
	}
	return reason
}

func (v *Verifier) check(ctx context.Context, item map[string]any) (string, error) {
	pinned := v.load()
	if len(pinned) == 0 {
		var err error
		if pinned, err = v.refresh(ctx, pinned); err != nil {
			return "", err
		}
	}
	signature, _ := item["signature"].(string)
	keyID, _ := item["key_id"].(string)
	if signature == "" || keyID == "" {
		if len(pinned) == 0 && v.serverSigns != nil && !*v.serverSigns {
			return "", nil // an older control plane that does not sign yet
		}
		return "Desired software is not signed; refusing to install it.", nil
	}
	if _, ok := pinned[keyID]; !ok {
		var err error
		if pinned, err = v.refresh(ctx, pinned); err != nil {
			return "", err
		}
	}
	public, ok := pinned[keyID]
	if !ok {
		return fmt.Sprintf("Desired software is signed by an untrusted key (%s); refusing to install it.", keyID), nil
	}
	message, err := Canonical(DesiredPayload(v.DeviceID, item))
	if err != nil {
		return "", err
	}
	if !verify(public, append([]byte(desiredContext), message...), signature) {
		return "Desired software signature does not verify; refusing to install it.", nil
	}
	return "", nil
}

// DesiredPayload is what a signature covers; it must match the server's
// signed_desired_payload. Missing fields are null.
func DesiredPayload(deviceID string, item map[string]any) map[string]any {
	out := map[string]any{"device_id": deviceID}
	for _, k := range []string{
		"workload_key", "kind", "release_id", "release_version", "artifact_id", "artifact_sha256", "revision", "spec",
	} {
		out[k] = item[k]
	}
	return out
}

// load returns the pinned keys, or none when the file is missing, damaged, or
// belongs to another device or token.
func (v *Verifier) load() map[string]string {
	var p pins
	if _, err := v.Store.Read(PinsFile, &p); err != nil {
		if !errors.Is(err, fs.ErrNotExist) {
			v.Log.Warn("signing pins unreadable; pinning again", "err", err)
		}
		return map[string]string{}
	}
	if p.DeviceID != "" && p.DeviceID != v.DeviceID {
		return map[string]string{}
	}
	if v.Fingerprint != "" && p.CredentialFP != v.Fingerprint {
		return map[string]string{}
	}
	if p.Keys == nil {
		return map[string]string{}
	}
	return p.Keys
}

func (v *Verifier) save(keys map[string]string) error {
	return v.Store.Write(PinsFile, pinsVersion, pins{Keys: keys, DeviceID: v.DeviceID, CredentialFP: v.Fingerprint})
}

// refresh pins every key on first use; afterwards only keys a pinned key endorsed.
func (v *Verifier) refresh(ctx context.Context, pinned map[string]string) (map[string]string, error) {
	fetched, err := v.Fetch(ctx)
	if errors.Is(err, ErrNotSigning) {
		v.serverSigns = new(bool)
		return pinned, nil
	}
	if err != nil {
		return nil, err
	}
	signs := true
	v.serverSigns = &signs
	if len(pinned) == 0 {
		pinned = map[string]string{}
		for _, k := range fetched {
			if k.KeyID != "" && k.PublicKey != "" {
				pinned[k.KeyID] = k.PublicKey
			}
		}
		if len(pinned) > 0 {
			ids := make([]string, 0, len(pinned))
			for id := range pinned {
				ids = append(ids, id)
			}
			sort.Strings(ids)
			v.Log.Warn("pinned organization signing keys on first use", "keys", strings.Join(ids, ", "))
			if err := v.save(pinned); err != nil {
				return nil, err
			}
		}
		return pinned, nil
	}
	added := false
	for changed := true; changed; {
		changed = false
		for _, k := range fetched {
			endorser, ok := pinned[k.EndorsedBy]
			if k.KeyID == "" || pinned[k.KeyID] != "" || !ok || k.Endorsement == "" {
				continue
			}
			message, err := Canonical(map[string]any{"key_id": k.KeyID, "public_key": k.PublicKey})
			if err != nil {
				return nil, err
			}
			if verify(endorser, append([]byte(endorsementContext), message...), k.Endorsement) {
				pinned[k.KeyID] = k.PublicKey
				v.Log.Info("pinned rotated signing key", "key", k.KeyID, "endorsed_by", k.EndorsedBy)
				changed, added = true, true
			}
		}
	}
	if added {
		if err := v.save(pinned); err != nil {
			return nil, err
		}
	}
	return pinned, nil
}

// verify checks an Ed25519 signature; both key and signature are base64url.
func verify(publicKey string, message []byte, signature string) bool {
	pub, err := b64url(publicKey)
	if err != nil || len(pub) != ed25519.PublicKeySize {
		return false
	}
	sig, err := b64url(signature)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(ed25519.PublicKey(pub), message, sig)
}

// b64url decodes base64url with or without padding, as Python's decoder does.
func b64url(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
}
