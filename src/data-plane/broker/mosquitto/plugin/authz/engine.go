// Package authz decides MQTT logins and topic access for the Mosquitto plugin.
//
// It holds no rules of its own. Every decision comes from the control plane
// (POST /internal/mqtt/authenticate and /authorize), the same endpoints EMQX
// calls, so the policy lives in one place whichever broker runs. This package
// only adds a short cache, a superuser shortcut, and the "disconnect this
// user" control message. It has no cgo so it can be unit tested on its own.
package authz

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Mosquitto ACL access values (mosquitto.h).
const (
	AccessRead        = 1
	AccessWrite       = 2
	AccessSubscribe   = 4
	AccessUnsubscribe = 8
)

// DefaultKickTopic must match KICK_TOPIC in the control plane (app/mqtt/policy.py).
const DefaultKickTopic = "meteorcloud/control/disconnect"

// Config is everything the engine needs. Zero values get safe defaults.
type Config struct {
	// ControlPlaneURL is the base URL, for example http://backend:8000.
	ControlPlaneURL string
	// InternalToken is sent as X-MQTT-Internal-Token.
	InternalToken string
	// Timeout bounds every HTTP call. The broker waits on it, so keep it short.
	Timeout time.Duration
	// AllowTTL / DenyTTL bound how long a topic decision is reused.
	AllowTTL time.Duration
	DenyTTL  time.Duration
	// MaxCacheEntries caps memory; the cache is cleared when it is exceeded.
	MaxCacheEntries int
	// KickTopic is the platform-only topic that disconnects a user.
	KickTopic string
	// HTTPClient overrides the client (tests).
	HTTPClient *http.Client
	// Now overrides the clock (tests).
	Now func() time.Time
}

func (c Config) withDefaults() Config {
	if c.Timeout <= 0 {
		c.Timeout = 3 * time.Second
	}
	if c.AllowTTL < 0 {
		c.AllowTTL = 0
	}
	if c.DenyTTL < 0 {
		c.DenyTTL = 0
	}
	if c.MaxCacheEntries <= 0 {
		c.MaxCacheEntries = 100000
	}
	if c.KickTopic == "" {
		c.KickTopic = DefaultKickTopic
	}
	if c.HTTPClient == nil {
		c.HTTPClient = &http.Client{Timeout: c.Timeout}
	}
	if c.Now == nil {
		c.Now = time.Now
	}
	c.ControlPlaneURL = strings.TrimRight(c.ControlPlaneURL, "/")
	return c
}

type cacheEntry struct {
	allowed bool
	expires time.Time
}

// Engine is safe for concurrent use.
type Engine struct {
	cfg Config

	mu         sync.Mutex
	cache      map[string]cacheEntry
	superusers map[string]struct{}
}

// New builds an engine. It fails when the control plane URL or token is missing,
// so a misconfigured broker refuses to start instead of running open or dead.
func New(cfg Config) (*Engine, error) {
	cfg = cfg.withDefaults()
	if cfg.ControlPlaneURL == "" {
		return nil, fmt.Errorf("control plane URL is required")
	}
	if cfg.InternalToken == "" {
		return nil, fmt.Errorf("MQTT internal token is required")
	}
	return &Engine{
		cfg:        cfg,
		cache:      map[string]cacheEntry{},
		superusers: map[string]struct{}{},
	}, nil
}

type authRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
	ClientID string `json:"clientid"`
}

type authResponse struct {
	Result      string `json:"result"`
	IsSuperuser bool   `json:"is_superuser"`
}

type aclRequest struct {
	Username string `json:"username"`
	ClientID string `json:"clientid"`
	Topic    string `json:"topic"`
	Action   string `json:"action"`
	Retain   bool   `json:"retain"`
}

type aclResponse struct {
	Result string `json:"result"`
}

// Authenticate checks a CONNECT. Any error denies (fail closed). Logins are
// never cached: a revoked device must be refused on its very next attempt.
func (e *Engine) Authenticate(clientID, username, password string) (bool, error) {
	if username == "" {
		return false, nil
	}
	var out authResponse
	err := e.post("/internal/mqtt/authenticate", authRequest{Username: username, Password: password, ClientID: clientID}, &out)
	allowed := err == nil && out.Result == "allow"

	e.mu.Lock()
	defer e.mu.Unlock()
	if allowed && out.IsSuperuser {
		e.superusers[username] = struct{}{}
	} else {
		// A failed login or a demotion drops the shortcut for this username.
		delete(e.superusers, username)
	}
	return allowed, err
}

// ActionFor maps a Mosquitto access value to the policy action name.
func ActionFor(access int) (string, bool) {
	switch access {
	case AccessWrite:
		return "publish", true
	case AccessSubscribe:
		return "subscribe", true
	case AccessRead:
		return "read", true
	default:
		return "", false
	}
}

// Authorize checks one publish, subscribe, or delivery. Any error denies.
func (e *Engine) Authorize(clientID, username string, access int, topic string, retain bool) (bool, error) {
	if username == "" {
		return false, nil
	}
	if access == AccessUnsubscribe {
		return true, nil // leaving a topic never leaks data
	}
	action, ok := ActionFor(access)
	if !ok {
		return false, nil
	}
	// Retain only matters for publishes; keep it out of other cache keys.
	if action != "publish" {
		retain = false
	}

	key := cacheKey(username, clientID, action, topic, retain)
	now := e.cfg.Now()
	e.mu.Lock()
	if _, super := e.superusers[username]; super {
		e.mu.Unlock()
		return true, nil
	}
	if entry, hit := e.cache[key]; hit && now.Before(entry.expires) {
		e.mu.Unlock()
		return entry.allowed, nil
	}
	e.mu.Unlock()

	var out aclResponse
	req := aclRequest{Username: username, ClientID: clientID, Topic: topic, Action: action, Retain: retain}
	if err := e.post("/internal/mqtt/authorize", req, &out); err != nil {
		return false, err // not cached: retry as soon as the control plane is back
	}
	allowed := out.Result == "allow"
	ttl := e.cfg.DenyTTL
	if allowed {
		ttl = e.cfg.AllowTTL
	}
	if ttl > 0 {
		e.mu.Lock()
		if len(e.cache) >= e.cfg.MaxCacheEntries {
			e.cache = map[string]cacheEntry{}
		}
		e.cache[key] = cacheEntry{allowed: allowed, expires: now.Add(ttl)}
		e.mu.Unlock()
	}
	return allowed, nil
}

type kickMessage struct {
	Username string `json:"username"`
}

// KickTarget inspects an inbound publish. When it is a valid disconnect request
// from a platform superuser it returns the username to disconnect and forgets
// every cached decision for that user.
func (e *Engine) KickTarget(publisherUsername, topic string, payload []byte) (string, bool) {
	if topic != e.cfg.KickTopic {
		return "", false
	}
	e.mu.Lock()
	_, super := e.superusers[publisherUsername]
	e.mu.Unlock()
	if !super {
		return "", false
	}
	var msg kickMessage
	if err := json.Unmarshal(payload, &msg); err != nil || msg.Username == "" {
		return "", false
	}
	if msg.Username == publisherUsername {
		return "", false // the platform never disconnects itself
	}
	e.Forget(msg.Username)
	return msg.Username, true
}

// Forget drops every cached decision for a username.
func (e *Engine) Forget(username string) {
	prefix := username + "\x00"
	e.mu.Lock()
	defer e.mu.Unlock()
	delete(e.superusers, username)
	for key := range e.cache {
		if strings.HasPrefix(key, prefix) {
			delete(e.cache, key)
		}
	}
}

func cacheKey(username, clientID, action, topic string, retain bool) string {
	r := "0"
	if retain {
		r = "1"
	}
	return username + "\x00" + clientID + "\x00" + action + "\x00" + r + "\x00" + topic
}

func (e *Engine) post(path string, body any, out any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), e.cfg.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.cfg.ControlPlaneURL+path, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-MQTT-Internal-Token", e.cfg.InternalToken)
	resp, err := e.cfg.HTTPClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("control plane answered HTTP %d", resp.StatusCode)
	}
	return json.Unmarshal(data, out)
}
