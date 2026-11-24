package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/identity"
)

// SignMQTTToken builds the short-lived HS256 JWT an application presents to the
// broker as its MQTT password. The broker verifies the signature, the username claim
// and the expiry itself, and enforces the embedded per-identity topic ACL, so an MQTT
// connection needs neither the control plane nor this service to be reachable.
func SignMQTTToken(secret []byte, id *identity.Identity, now, expires time.Time) (username, token string) {
	username = bus.AppUsernamePrefix + id.CredentialID
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	claims, _ := json.Marshal(map[string]any{
		"username": username,
		"iat":      now.Unix(),
		"exp":      expires.Unix(),
		"acl":      signedACL(bus.BuildACL(id)),
	})
	signing := header + "." + base64.RawURLEncoding.EncodeToString(claims)
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(signing))
	return username, signing + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// aclRule is one entry of the token's ACL, in the broker's rule format.
type aclRule struct {
	Permission string `json:"permission"`
	Action     string `json:"action"`
	Topic      string `json:"topic"`
	Retain     *bool  `json:"retain,omitempty"`
}

// signedACL is the ACL as the broker enforces it.
//
// Subscriptions are exact ("eq"): EMQX matches a requested filter like a topic, so
// a rule ending in "+" would also allow a subscription ending in "#" and reach
// streams outside the permission. A client therefore subscribes to exactly the
// filters it was given.
//
// Publishes are allowed only when not retained: a retained message would be
// replayed by the broker to later subscribers as if it were new.
//
// The list ends with a deny for everything else, so an identity without grants
// can do nothing whatever other authorization the broker has configured.
func signedACL(acl bus.ACL) []aclRule {
	notRetained := false
	rules := make([]aclRule, 0, len(acl.Publish)+len(acl.Subscribe)+1)
	for _, topic := range acl.Publish {
		rules = append(rules, aclRule{Permission: "allow", Action: "publish", Topic: topic, Retain: &notRetained})
	}
	for _, filter := range acl.Subscribe {
		rules = append(rules, aclRule{Permission: "allow", Action: "subscribe", Topic: "eq " + filter})
	}
	return append(rules, aclRule{Permission: "deny", Action: "all", Topic: "#"})
}

// handleMQTTCredentials exchanges the application token for MQTT connection details.
// For clients that speak MQTT directly; the SDK's default transport does not need it.
func (s *Server) handleMQTTCredentials(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	if s.opts.MQTTJWTSecret == "" {
		writeError(w, http.StatusNotFound, "mqtt_not_enabled", "Direct MQTT access is not enabled on this installation.")
		return
	}
	ttl := s.opts.MQTTTokenTTL
	if ttl <= 0 {
		ttl = time.Hour
	}
	now := time.Now()
	expires := now.Add(ttl)
	if id.ExpiresAt.Before(expires) {
		expires = id.ExpiresAt // never outlive the application credential
	}
	username, token := SignMQTTToken([]byte(s.opts.MQTTJWTSecret), id, now, expires)
	acl := bus.BuildACL(id)
	s.metrics.MQTTAuth.WithLabelValues("token", "issued").Inc()
	device := id.DeviceID
	if device == "" {
		device = bus.CloudDevice
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]any{
		"url":        s.opts.MQTTPublicURL,
		"username":   username,
		"password":   token,
		"client_id":  username,
		"expires_at": expires.UTC().Format(time.RFC3339),
		"topic_root": bus.TopicPrefix + id.OrganizationID + "/apps/" + id.Application + "/devices/" + device,
		"live_root":  bus.TopicPrefix + id.OrganizationID + "/live",
		"publish":    acl.Publish,
		"subscribe":  acl.Subscribe,
	})
}
