package network

import (
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"meteorcloud/meteorcli/internal/config"
)

// The WireGuard key pair lives in identity/wireguard (private.key 0600,
// public.key). It is made here with crypto/ecdh, as `wg genkey | wg pubkey`
// would, and the private key never leaves the device: only the helper sees
// it, to write wg0's config.

// hostKeyTypes are the sshd host key types the server pins.
var hostKeyTypes = map[string]bool{"ssh-ed25519": true, "ecdsa-sha2-nistp256": true, "ssh-rsa": true}

// readKeypair returns the stored key pair (base64), or an error when either
// file is missing or not a key.
func readKeypair(dir string) (priv, pub string, err error) {
	read := func(name string) (string, error) {
		data, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return "", err
		}
		v := strings.TrimSpace(string(data))
		if raw, err := base64.StdEncoding.DecodeString(v); err != nil || len(raw) != 32 {
			return "", fmt.Errorf("%s is not a WireGuard key", name)
		}
		return v, nil
	}
	if priv, err = read("private.key"); err != nil {
		return "", "", err
	}
	if pub, err = read("public.key"); err != nil {
		return "", "", err
	}
	return priv, pub, nil
}

// ensureKeypair returns the stored key pair, making one when there is none.
// Keys imported from the Python agent are used as they are.
func ensureKeypair(dir string) (priv, pub string, err error) {
	if priv, pub, err = readKeypair(dir); err == nil {
		return priv, pub, nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", "", fmt.Errorf("wireguard keys: %w", err)
	}
	priv, pub, err = newKeypair()
	if err != nil {
		return "", "", err
	}
	if err := config.WriteFileAtomic(filepath.Join(dir, "private.key"), []byte(priv+"\n"), 0o600); err != nil {
		return "", "", fmt.Errorf("wireguard keys: %w", err)
	}
	if err := config.WriteFileAtomic(filepath.Join(dir, "public.key"), []byte(pub+"\n"), 0o600); err != nil {
		return "", "", fmt.Errorf("wireguard keys: %w", err)
	}
	return priv, pub, nil
}

// newKeypair makes a Curve25519 key pair, clamped like `wg genkey`.
func newKeypair() (priv, pub string, err error) {
	var raw [32]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", "", fmt.Errorf("wireguard keys: %w", err)
	}
	raw[0] &= 248
	raw[31] = raw[31]&127 | 64
	key, err := ecdh.X25519().NewPrivateKey(raw[:])
	if err != nil {
		return "", "", fmt.Errorf("wireguard keys: %w", err)
	}
	return base64.StdEncoding.EncodeToString(raw[:]), base64.StdEncoding.EncodeToString(key.PublicKey().Bytes()), nil
}

// publicKeyOf derives the public key of a base64 private key (tests).
func publicKeyOf(priv string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(priv)
	if err != nil || len(raw) != 32 {
		return "", errors.New("not a WireGuard key")
	}
	key, err := ecdh.X25519().NewPrivateKey(raw)
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(key.PublicKey().Bytes()), nil
}

// readHostKey returns "<type> <base64>" from an sshd host public key file, ""
// when it is missing or of a type the server does not take.
func readHostKey(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	f := strings.Fields(string(data))
	if len(f) < 2 || !hostKeyTypes[f[0]] {
		return ""
	}
	return f[0] + " " + f[1]
}
