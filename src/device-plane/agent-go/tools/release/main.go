// Command release writes and signs the release manifest of the Go meteorcli
// (meteorcli-release.json and its .sig) that `meteorcli updater` verifies.
// It is a build tool, never shipped to devices.
//
//	go run ./tools/release genkey                 # new key pair: keep the private key secret
//	go run ./tools/release sign <dir> <version>   # needs $METEORCLI_RELEASE_SIGNING_KEY
//
// The private key is the base64 (standard) ed25519 private key; the public key
// goes into internal/update/key.go (or -X ...update.releaseKeys= for test builds).
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/update"
)

// KeyEnv names the variable that holds the private signing key.
const KeyEnv = "METEORCLI_RELEASE_SIGNING_KEY"

func main() {
	if err := run(os.Args[1:], os.Getenv(KeyEnv), os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "release:", err)
		os.Exit(1)
	}
}

func run(args []string, key string, out io.Writer) error {
	if len(args) == 0 {
		return errors.New("usage: release genkey | release sign <dir> <version>")
	}
	switch args[0] {
	case "genkey":
		pub, priv, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			return err
		}
		fmt.Fprintf(out, "private %s\npublic %s\n", base64.StdEncoding.EncodeToString(priv), base64.StdEncoding.EncodeToString(pub))
		return nil
	case "sign":
		if len(args) != 3 {
			return errors.New("usage: release sign <dir> <version>")
		}
		priv, err := parsePrivate(key)
		if err != nil {
			return err
		}
		return sign(args[1], args[2], priv, out)
	}
	return fmt.Errorf("unknown command %q", args[0])
}

func parsePrivate(s string) (ed25519.PrivateKey, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil || len(raw) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("$%s must hold a base64 ed25519 private key (release genkey)", KeyEnv)
	}
	return ed25519.PrivateKey(raw), nil
}

// sign hashes every meteorcli-linux-* binary in dir and writes the manifest
// and its signature next to them.
func sign(dir, version string, priv ed25519.PrivateKey, out io.Writer) error {
	paths, err := filepath.Glob(filepath.Join(dir, "meteorcli-linux-*"))
	if err != nil {
		return err
	}
	sort.Strings(paths)
	m := update.Manifest{Schema: update.ManifestSchema, Version: version, Files: map[string]update.File{}}
	for _, p := range paths {
		f, err := hashFile(p)
		if err != nil {
			return err
		}
		m.Files[filepath.Base(p)] = f
	}
	if len(m.Files) == 0 {
		return fmt.Errorf("no meteorcli-linux-* binaries in %s", dir)
	}
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	// The updater refuses a manifest it cannot verify: check before publishing.
	if _, err := update.VerifyManifest(raw, sigOf(priv, raw), []ed25519.PublicKey{priv.Public().(ed25519.PublicKey)}); err != nil {
		return err
	}
	if err := config.WriteFileAtomic(filepath.Join(dir, update.ManifestName), raw, 0o644); err != nil {
		return err
	}
	if err := config.WriteFileAtomic(filepath.Join(dir, update.SignatureName), sigOf(priv, raw), 0o644); err != nil {
		return err
	}
	fmt.Fprintf(out, "signed %s (%d binaries) with key %s\n", version, len(m.Files),
		base64.StdEncoding.EncodeToString(priv.Public().(ed25519.PublicKey)))
	return nil
}

func sigOf(priv ed25519.PrivateKey, raw []byte) []byte {
	return []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, raw)) + "\n")
}

func hashFile(path string) (update.File, error) {
	f, err := os.Open(path)
	if err != nil {
		return update.File{}, err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return update.File{}, fmt.Errorf("hash %s: %w", path, err)
	}
	return update.File{SHA256: hex.EncodeToString(h.Sum(nil)), Size: n}, nil
}
