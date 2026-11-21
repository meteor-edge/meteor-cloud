package helper

import (
	"context"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
)

// syscallNoFollow refuses to write through a planted symlink.
const syscallNoFollow = syscall.O_NOFOLLOW

// uuidRe is a change id (the server's UUID).
var uuidRe = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// netplanIDRe is a netplan definition id: an interface name or NM-<uuid>, never
// a dot (which `netplan set` paths use).
var netplanIDRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// plainKeyRe is an SSID that can be a `netplan set` path element.
var plainKeyRe = regexp.MustCompile(`^[^.=]+$`)

func sortStrings(s []string) { sort.Strings(s) }

// capWriter keeps the first max bytes written to w.
type capWriter struct {
	w      interface{ Write([]byte) (int, error) }
	n, max int
}

func (c *capWriter) Write(p []byte) (int, error) {
	if room := c.max - c.n; room > 0 {
		k := min(len(p), room)
		_, _ = c.w.Write(p[:k])
		c.n += k
	}
	return len(p), nil
}

// netplanDefinition returns the netplan definition a generated profile comes
// from, or "" when it is not one. Netplan names a Wi-Fi profile
// netplan-<definition>-<ssid>; one saved through netplan lives in
// netplan-NM-<uuid>.nmconnection.
func netplanDefinition(p nmProfile) string {
	base := filepath.Base(p.file)
	stem, ok := strings.CutPrefix(base, netplanPrefix)
	if !ok {
		return ""
	}
	stem = strings.TrimSuffix(stem, nmKeyfileExtension)
	var def string
	switch suffix := "-" + p.ssid; {
	case strings.HasPrefix(stem, "NM-"):
		def = stem
	case strings.HasPrefix(p.name, netplanPrefix) && strings.HasSuffix(p.name, suffix) && len(p.name) > len(netplanPrefix)+len(suffix):
		def = p.name[len(netplanPrefix) : len(p.name)-len(suffix)]
	default:
		return ""
	}
	if !netplanIDRe.MatchString(def) {
		return ""
	}
	return def
}

// forgetInNetplan deletes ssid from the netplan definitions behind profiles,
// regenerates, and deletes the generated profiles. It reports whether every
// definition could be changed.
func (h Host) forgetInNetplan(ctx context.Context, profiles []nmProfile, ssid string) bool {
	look := h.LookNetplan
	if look == nil {
		look = func() bool { _, err := lookTool("netplan"); return err == nil }
	}
	if !look() {
		return false
	}
	everything := h.wifiProfiles(ctx)
	done := true
	for _, p := range profiles {
		def := netplanDefinition(p)
		if def == "" {
			done = false
			continue
		}
		others := false
		for _, o := range everything {
			if o.ssid != ssid && netplanDefinition(o) == def {
				others = true
			}
		}
		key := "wifis." + def
		if others {
			// The definition has other networks: drop only this one.
			if !plainKeyRe.MatchString(ssid) {
				done = false
				continue
			}
			key += ".access-points." + ssid
		}
		if _, err := h.Run(ctx, "netplan", "set", key+"=null"); err != nil {
			done = false
		}
	}
	_, _ = h.Run(ctx, "netplan", "generate")
	for _, p := range profiles {
		_, _ = h.nmcli(ctx, "connection", "delete", "uuid", p.uuid)
	}
	_, _ = h.nmcli(ctx, "connection", "reload")
	return done
}
