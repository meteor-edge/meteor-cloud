package config

import (
	"errors"
	"net/netip"
	"strings"
)

// DeriveAPIBase turns a domain, IP or URL into an API origin, exactly like the
// Python agent: the configured host is the API (nothing is rewritten to api.<host>),
// IPs and localhost use HTTP, and a leftover api.<ip> from old configs is stripped.
func DeriveAPIBase(domain string, forceHTTP bool) (string, error) {
	scheme, host := splitSchemeHost(domain)
	if host == "" {
		return "", errors.New("domain is empty")
	}
	if strings.HasPrefix(strings.ToLower(host), "api.") && hostIsLocal(host[4:]) {
		host = host[4:]
	}
	useHTTP := forceHTTP || scheme == "http" || hostIsLocal(host)
	if useHTTP {
		return "http://" + host, nil
	}
	return "https://" + host, nil
}

// LANHostMissingPort reports a LAN or IP host without a port. The laptop
// `make dev` API listens on :8000, so the CLI prints a hint.
func LANHostMissingPort(value string) bool {
	_, host := splitSchemeHost(value)
	if host == "" || !hostIsLocal(host) {
		return false
	}
	return hostname(host) == host
}

func splitSchemeHost(value string) (scheme, host string) {
	raw := strings.TrimSpace(value)
	lower := strings.ToLower(raw)
	switch {
	case strings.HasPrefix(lower, "https://"):
		scheme, raw = "https", raw[len("https://"):]
	case strings.HasPrefix(lower, "http://"):
		scheme, raw = "http", raw[len("http://"):]
	}
	host, _, _ = strings.Cut(raw, "/")
	if strings.HasPrefix(strings.ToLower(host), "www.") {
		host = host[4:]
	}
	return scheme, host
}

// hostname strips the port: "[::1]:8000" -> "::1", "10.0.0.2:8000" -> "10.0.0.2".
func hostname(host string) string {
	if strings.HasPrefix(host, "[") {
		if end := strings.Index(host, "]"); end > 0 {
			return host[1:end]
		}
	}
	if strings.Count(host, ":") == 1 {
		h, _, _ := strings.Cut(host, ":")
		return h
	}
	return host
}

func hostIsLocal(host string) bool {
	h := strings.ToLower(hostname(host))
	if h == "localhost" {
		return true
	}
	_, err := netip.ParseAddr(h)
	return err == nil
}
