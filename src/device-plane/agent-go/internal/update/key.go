package update

// releaseKeys are the public keys that sign Go meteorcli releases (base64
// ed25519, comma separated; the private key is the release workflow's secret
// METEORCLI_RELEASE_SIGNING_KEY). The updater installs only a release whose
// manifest one of them signed. Test builds replace it with
// -ldflags "-X meteorcloud/meteorcli/internal/update.releaseKeys=<key>".
var releaseKeys = "U5auvXRpTnBqeCfK13ubamnDrlpR//408zdWFa1DfeQ="

// ReleaseKeys returns the compiled-in release public keys.
func ReleaseKeys() string { return releaseKeys }
