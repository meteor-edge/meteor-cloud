package api

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// Inventory is the device facts sent with register, enrollment and heartbeats
// (common.json#/$defs/inventory). The platform module fills it; keys are the
// contract's field names, so new facts need no change here.
type Inventory map[string]any

// MQTTConnect is how to reach the broker (common.json#/$defs/mqtt_connect).
// Password is a secret: never log it.
type MQTTConnect struct {
	Host     string  `json:"host"`
	Port     int     `json:"port"`
	Username string  `json:"username"`
	Password string  `json:"password"`
	TLS      *bool   `json:"tls"`
	CACert   *string `json:"ca_cert"`
}

// Enrollment is the identity the server hands out on register and approval.
// DeviceToken is a secret.
type Enrollment struct {
	DeviceID                 string       `json:"device_id"`
	DeviceToken              string       `json:"device_token"`
	OrganizationID           string       `json:"organization_id"`
	Name                     string       `json:"name"`
	HeartbeatIntervalSeconds int          `json:"heartbeat_interval_seconds"`
	MQTT                     *MQTTConnect `json:"mqtt"`
}

// EnrollCheck is the answer of /enroll/check.
type EnrollCheck struct {
	OK               bool    `json:"ok"`
	OrganizationID   string  `json:"organization_id"`
	OrganizationName string  `json:"organization_name"`
	KeyName          string  `json:"key_name"`
	KeyPrefix        string  `json:"key_prefix"`
	ExpiresAt        *string `json:"expires_at"`
}

// EnrollRequest is the answer of /enroll/request. ClaimSecret is a secret.
type EnrollRequest struct {
	RequestID           string  `json:"request_id"`
	ClaimSecret         string  `json:"claim_secret"`
	Status              string  `json:"status"`
	PollIntervalSeconds int     `json:"poll_interval_seconds"`
	ExpiresAt           *string `json:"expires_at"`
}

// Poll states of an enrollment request.
const (
	PollPending  = "pending"
	PollApproved = "approved"
	PollRejected = "rejected"
	PollExpired  = "expired"
)

// EnrollPoll is the answer of /enroll/poll. Identity fields are set only when
// Status is approved, and only once.
type EnrollPoll struct {
	Status              string  `json:"status"`
	PollIntervalSeconds int     `json:"poll_interval_seconds"`
	RejectionReason     *string `json:"rejection_reason"`
	Enrollment
}

// ModuleHealth is one entry of capabilities.modules (common.json#/$defs/module_health).
type ModuleHealth struct {
	State     string     `json:"state"`
	Restarts  int        `json:"restarts"`
	LastError *string    `json:"last_error,omitempty"`
	Since     *time.Time `json:"since,omitempty"`
}

// Capabilities says what this device has and what this agent can do.
type Capabilities struct {
	Systemd       bool                    `json:"systemd"`
	Docker        bool                    `json:"docker"`
	DockerCompose bool                    `json:"docker_compose"`
	WireGuard     bool                    `json:"wireguard"`
	SSH           bool                    `json:"ssh"`
	Actions       []string                `json:"actions"`
	DeployKinds   []string                `json:"deploy_kinds"`
	Modules       map[string]ModuleHealth `json:"modules,omitempty"`
}

// MarshalJSON always sends actions and deploy_kinds as arrays (never null), as
// the contract wants, without changing the caller's slices.
func (c Capabilities) MarshalJSON() ([]byte, error) {
	type plain Capabilities // same fields, no MarshalJSON method
	if c.Actions == nil {
		c.Actions = []string{}
	}
	if c.DeployKinds == nil {
		c.DeployKinds = []string{}
	}
	return json.Marshal(plain(c))
}

// HeartbeatRequest is the heartbeat body: the inventory with capabilities,
// metrics and software reports beside it.
type HeartbeatRequest struct {
	Inventory      Inventory
	Capabilities   *Capabilities
	Metrics        map[string]any
	SoftwareReport []json.RawMessage
}

// MarshalJSON flattens the inventory into the top-level object, as the contract wants.
func (h HeartbeatRequest) MarshalJSON() ([]byte, error) {
	body := make(map[string]any, len(h.Inventory)+3)
	for k, v := range h.Inventory {
		body[k] = v
	}
	if h.Capabilities != nil {
		body["capabilities"] = h.Capabilities
	}
	if h.Metrics != nil {
		body["metrics"] = h.Metrics
	}
	if h.SoftwareReport != nil {
		body["software_report"] = h.SoftwareReport
	}
	return json.Marshal(body)
}

// HeartbeatResponse says when to beat next and which work waits.
type HeartbeatResponse struct {
	DeviceID                 string            `json:"device_id"`
	Status                   string            `json:"status"`
	HeartbeatIntervalSeconds int               `json:"heartbeat_interval_seconds"`
	ServerTime               string            `json:"server_time"`
	DesiredSoftware          []json.RawMessage `json:"desired_software"`
	WifiPending              bool              `json:"wifi_pending"`
	ActionsPending           bool              `json:"actions_pending"`
}

// MQTTStatus is the answer of GET /mqtt.
type MQTTStatus struct {
	Enabled       bool    `json:"enabled"`
	ConfigVersion *string `json:"config_version"`
}

// MQTTCredentials is the answer of POST /mqtt/credentials. The old password stops working.
type MQTTCredentials struct {
	MQTTConnect
	ConfigVersion string `json:"config_version"`
}

// Health calls GET /api/v1/health (no auth) to see whether the server answers.
func (c *Client) Health(ctx context.Context) error {
	return c.do(ctx, http.MethodGet, "/api/v1/health", "", nil, nil)
}

// EnrollCheck checks an enrollment API key without creating a request.
func (c *Client) EnrollCheck(ctx context.Context, apiKey string) (*EnrollCheck, error) {
	var out EnrollCheck
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/enroll/check", apiKey, struct{}{}, &out)
}

// EnrollRequest asks an operator to approve this device.
func (c *Client) EnrollRequest(ctx context.Context, apiKey string, inv Inventory, name string) (*EnrollRequest, error) {
	var out EnrollRequest
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/enroll/request", apiKey, withFields(inv, "name", name), &out)
}

// EnrollPoll asks whether a request was approved. It sends no Authorization header.
func (c *Client) EnrollPoll(ctx context.Context, requestID, claimSecret string) (*EnrollPoll, error) {
	var out EnrollPoll
	body := map[string]string{"request_id": requestID, "claim_secret": claimSecret}
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/enroll/poll", "", body, &out)
}

// Register enrolls with a registration token. deviceToken, when set, proves this
// is the device already registered for this hardware; send it only to the server
// that issued it.
func (c *Client) Register(ctx context.Context, token string, inv Inventory, name, deviceToken string) (*Enrollment, error) {
	body := withFields(inv, "token", token, "name", name, "device_token", deviceToken)
	var out Enrollment
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/register", "", body, &out)
}

// Heartbeat reports the device and learns which work waits.
func (c *Client) Heartbeat(ctx context.Context, deviceToken string, req HeartbeatRequest) (*HeartbeatResponse, error) {
	var out HeartbeatResponse
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/heartbeat", deviceToken, req, &out)
}

// MQTTStatus returns whether MQTT is on and the current config version.
func (c *Client) MQTTStatus(ctx context.Context, deviceToken string) (*MQTTStatus, error) {
	var out MQTTStatus
	return &out, c.do(ctx, http.MethodGet, "/api/v1/agent/mqtt", deviceToken, nil, &out)
}

// MQTTCredentials issues a new MQTT password.
func (c *Client) MQTTCredentials(ctx context.Context, deviceToken string) (*MQTTCredentials, error) {
	var out MQTTCredentials
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/mqtt/credentials", deviceToken, struct{}{}, &out)
}

// withFields copies inv and adds the non-empty key/value pairs.
func withFields(inv Inventory, kv ...string) map[string]any {
	body := make(map[string]any, len(inv)+len(kv)/2)
	for k, v := range inv {
		body[k] = v
	}
	for i := 0; i+1 < len(kv); i += 2 {
		if kv[i+1] != "" {
			body[kv[i]] = kv[i+1]
		}
	}
	return body
}
