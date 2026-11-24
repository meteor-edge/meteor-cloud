package api

import (
	"context"
	"encoding/json"
	"net/http"
)

// WifiChange is one change from GET /network/wifi (network-wifi.response.json).
// Password is a secret: never log it.
type WifiChange struct {
	ID         string  `json:"id"`
	Revision   *int    `json:"revision"`
	Action     string  `json:"action"`
	SSID       string  `json:"ssid"`
	Security   string  `json:"security"`
	Hidden     bool    `json:"hidden"`
	ConnectNow bool    `json:"connect_now"`
	Password   *string `json:"password"`
}

// Wi-Fi result states (network-wifi-report.request.json).
const (
	WifiApplied    = "applied"
	WifiRemoved    = "removed"
	WifiRolledBack = "rolled_back"
	WifiFailed     = "failed"
)

// MaxWifiResults is the contract's limit of results in one report.
const MaxWifiResults = 64

// WifiResult says what happened to one change. Error is at most 500 characters.
type WifiResult struct {
	ID       string `json:"id"`
	Revision *int   `json:"revision"`
	State    string `json:"state"`
	Error    string `json:"error,omitempty"`
}

// WifiChanges collects the Wi-Fi changes waiting for this device (the only
// moment a Wi-Fi password travels).
func (c *Client) WifiChanges(ctx context.Context, deviceToken string) ([]WifiChange, error) {
	var out struct {
		Changes []WifiChange `json:"changes"`
	}
	if err := c.do(ctx, http.MethodGet, "/api/v1/agent/network/wifi", deviceToken, nil, &out); err != nil {
		return nil, err
	}
	return out.Changes, nil
}

// ReportWifi sends results and, when status is not nil, the device's Wi-Fi
// state (never a password).
func (c *Client) ReportWifi(ctx context.Context, deviceToken string, results []WifiResult, status json.RawMessage) error {
	body := map[string]any{"results": results}
	if results == nil {
		body["results"] = []WifiResult{}
	}
	if status != nil {
		body["status"] = status
	}
	return c.do(ctx, http.MethodPost, "/api/v1/agent/network/wifi/report", deviceToken, body, nil)
}

// WireGuardRequest registers the device's WireGuard public key and SSH facts
// (connectivity-wireguard.request.json). Empty optional fields are left out.
type WireGuardRequest struct {
	PublicKey      string `json:"public_key"`
	SSHUser        string `json:"ssh_user,omitempty"`
	SSHHostKey     string `json:"ssh_host_key,omitempty"`
	SSHCAPublicKey string `json:"ssh_ca_public_key,omitempty"`
}

// WireGuardResponse is the tunnel and SSH settings (connectivity-wireguard.response.json).
type WireGuardResponse struct {
	Status               string  `json:"status"`
	Provider             string  `json:"provider"`
	WGAddress            string  `json:"wg_address"`
	ServerPublicKey      string  `json:"server_public_key"`
	ServerEndpoint       string  `json:"server_endpoint"`
	AllowedIPs           string  `json:"allowed_ips"`
	PersistentKeepalive  int     `json:"persistent_keepalive"`
	SSHUser              string  `json:"ssh_user"`
	SSHGatewayPublicKey  *string `json:"ssh_gateway_public_key"`
	SSHGatewayKeyRetired bool    `json:"ssh_gateway_key_retired"`
	SSHCAPublicKey       *string `json:"ssh_ca_public_key"`
	SSHPrincipal         *string `json:"ssh_principal"`
}

// RegisterWireGuard registers the public key (the private key never leaves
// the device) and returns the tunnel settings.
func (c *Client) RegisterWireGuard(ctx context.Context, deviceToken string, req WireGuardRequest) (*WireGuardResponse, error) {
	var out WireGuardResponse
	return &out, c.do(ctx, http.MethodPost, "/api/v1/agent/connectivity/wireguard", deviceToken, req, &out)
}
