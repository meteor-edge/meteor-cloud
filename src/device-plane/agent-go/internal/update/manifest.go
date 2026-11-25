package update

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"runtime"
	"strings"
)

// Release file names, next to the binaries in a release.
const (
	// ManifestName is the release manifest: version and the SHA-256 of every binary.
	ManifestName = "meteorcli-release.json"
	// SignatureName holds the base64 ed25519 signature of the manifest's exact bytes.
	SignatureName = ManifestName + ".sig"
	// ManifestSchema is the manifest format this agent reads.
	ManifestSchema = 1
)

// maxBinary bounds a downloaded binary (the build budget is 15 MB).
const maxBinary = 64 << 20

// Manifest is the signed description of one release. Signing the manifest,
// not each binary, binds the version to the checksums: an old signed release
// cannot be passed off as a new one, and the checksum still covers the binary.
type Manifest struct {
	Schema  int             `json:"schema"`
	Version string          `json:"version"`
	Files   map[string]File `json:"files"`
}

// File is one binary of a release.
type File struct {
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

// ErrUnsigned means the manifest has no valid signature from a release key.
var ErrUnsigned = errors.New("release is not signed by a MeteorCloud release key")

// ParseKeys reads release public keys: base64 (standard) ed25519 keys
// separated by commas. Several keys allow a key rotation.
func ParseKeys(s string) ([]ed25519.PublicKey, error) {
	var keys []ed25519.PublicKey
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		raw, err := base64.StdEncoding.DecodeString(part)
		if err != nil || len(raw) != ed25519.PublicKeySize {
			return nil, fmt.Errorf("release key %q is not a base64 ed25519 public key", part)
		}
		keys = append(keys, ed25519.PublicKey(raw))
	}
	if len(keys) == 0 {
		return nil, errors.New("no release key is compiled into this build")
	}
	return keys, nil
}

// VerifyManifest checks sig (the .sig file) over raw with any of keys, then
// parses the manifest. Nothing in raw is trusted before the signature checks.
func VerifyManifest(raw, sig []byte, keys []ed25519.PublicKey) (*Manifest, error) {
	s, err := base64.StdEncoding.DecodeString(string(bytes.TrimSpace(sig)))
	if err != nil || len(s) != ed25519.SignatureSize {
		return nil, ErrUnsigned
	}
	ok := false
	for _, k := range keys {
		if ed25519.Verify(k, raw, s) {
			ok = true
			break
		}
	}
	if !ok {
		return nil, ErrUnsigned
	}
	var m Manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("release manifest: %w", err)
	}
	if m.Schema != ManifestSchema {
		return nil, fmt.Errorf("release manifest schema %d is not %d", m.Schema, ManifestSchema)
	}
	if _, err := parseVersion(m.Version); err != nil {
		return nil, fmt.Errorf("release manifest: %w", err)
	}
	return &m, nil
}

// Binary returns the file entry for name, checked for shape.
func (m *Manifest) Binary(name string) (File, error) {
	f, ok := m.Files[name]
	if !ok {
		return File{}, fmt.Errorf("release %s has no %s", m.Version, name)
	}
	if b, err := hex.DecodeString(f.SHA256); err != nil || len(b) != 32 {
		return File{}, fmt.Errorf("release %s: bad sha256 for %s", m.Version, name)
	}
	if f.Size <= 0 || f.Size > maxBinary {
		return File{}, fmt.Errorf("release %s: bad size %d for %s", m.Version, f.Size, name)
	}
	return f, nil
}

// AssetName is the release binary for this CPU, as scripts/build.sh names it.
// 32-bit ARM ships one ARMv6 build that runs on every Raspberry Pi.
func AssetName() (string, error) {
	switch runtime.GOARCH {
	case "arm64", "amd64":
		return "meteorcli-linux-" + runtime.GOARCH, nil
	case "arm":
		return "meteorcli-linux-armv6", nil
	}
	return "", fmt.Errorf("no release binary for %s", runtime.GOARCH)
}
