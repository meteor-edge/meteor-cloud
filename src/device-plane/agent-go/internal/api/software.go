package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
)

// Software lists the desired software for this device (software.response.json).
// Items are returned as decoded JSON (numbers kept as json.Number) because the
// signature covers their exact content, including spec fields this agent does
// not know.
func (c *Client) Software(ctx context.Context, deviceToken string) ([]map[string]any, error) {
	var raw json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/api/v1/agent/software", deviceToken, nil, &raw); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var items []any
	if err := dec.Decode(&items); err != nil || items == nil {
		// Not a list. Treating it as "nothing desired" would let the cycle
		// drop stored application credentials, so it is an error.
		return nil, &Error{Code: "bad_response", Message: "The desired software list is not a JSON array."}
	}
	out := make([]map[string]any, 0, len(items))
	for _, it := range items {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out, nil
}

// SigningKey is one organization public key (signing-keys.response.json).
type SigningKey struct {
	KeyID       string  `json:"key_id"`
	Algorithm   string  `json:"algorithm"`
	PublicKey   string  `json:"public_key"`
	EndorsedBy  *string `json:"endorsed_by"`
	Endorsement *string `json:"endorsement"`
}

// SigningKeys returns the organization's keys. A 404 (the server does not
// sign) comes back as an *Error with Status 404.
func (c *Client) SigningKeys(ctx context.Context, deviceToken string) ([]SigningKey, error) {
	var out []SigningKey
	if err := c.do(ctx, http.MethodGet, "/api/v1/agent/signing-keys", deviceToken, nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// AppCredential is a device-scoped application token
// (applications-credentials.response.json). Token is a secret: never log it.
type AppCredential struct {
	Token          string  `json:"token"`
	CredentialID   string  `json:"credential_id"`
	ExpiresAt      string  `json:"expires_at"`
	RefreshAfter   string  `json:"refresh_after"`
	Endpoint       *string `json:"endpoint"`
	Application    string  `json:"application"`
	OrganizationID string  `json:"organization_id"`
	DeviceID       string  `json:"device_id"`
	Environment    string  `json:"environment"`
}

// AppCredentials mints a token for the application a workload is bound to. The
// previous token stays valid for a short grace period.
func (c *Client) AppCredentials(ctx context.Context, deviceToken, workloadKey string) (*AppCredential, error) {
	var out AppCredential
	body := map[string]string{"workload_key": workloadKey}
	if err := c.do(ctx, http.MethodPost, "/api/v1/agent/applications/credentials", deviceToken, body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// SoftwareSnapshot sends what one workload is made of (software-snapshots.request.json).
func (c *Client) SoftwareSnapshot(ctx context.Context, deviceToken string, snapshot any) error {
	body := map[string]any{"snapshot": snapshot}
	return c.do(ctx, http.MethodPost, "/api/v1/agent/software/snapshots", deviceToken, body, nil)
}
