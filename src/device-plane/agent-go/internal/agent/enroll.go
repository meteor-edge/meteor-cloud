package agent

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"meteorcloud/meteorcli/internal/identity"
	"meteorcloud/meteorcli/internal/localrpc"
)

// MaxClaimWait bounds `meteorcli claim --wait` and `request-token --wait`.
const MaxClaimWait = 30 * time.Minute

// RegisterParams is the body of register. Token is a secret.
type RegisterParams struct {
	Token string `json:"token"`
	Name  string `json:"name"`
}

// RequestParams is the body of enroll.request. APIKey (a secret) is optional:
// without it the saved key is used.
type RequestParams struct {
	APIKey string `json:"api_key"`
	Name   string `json:"name"`
	New    bool   `json:"new"`
}

// ClaimParams is the body of enroll.claim.
type ClaimParams struct {
	WaitSeconds int `json:"wait_seconds"`
}

// enrollHandlers serves the methods the identity service performs. The agent
// does the work and keeps the secrets; the CLI only passes on what the user
// typed and prints the answer.
func (m *SocketModule) enrollHandlers() map[string]localrpc.Handler {
	return map[string]localrpc.Handler{
		"register": {
			Timeout: time.Minute,
			Audit: func(raw json.RawMessage) []any {
				var p RegisterParams
				_ = json.Unmarshal(raw, &p)
				return []any{"name", p.Name}
			},
			Run: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
				var p RegisterParams
				if err := json.Unmarshal(raw, &p); err != nil || p.Token == "" {
					return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params must be {\"token\": \"...\", \"name\": \"...\"}")
				}
				id, err := m.identityService()
				if err != nil {
					return nil, err
				}
				res, err := id.Register(ctx, p.Token, p.Name)
				return res, toRPC(err)
			},
		},
		"enroll.request": {
			Timeout: time.Minute,
			Audit: func(raw json.RawMessage) []any {
				var p RequestParams
				_ = json.Unmarshal(raw, &p)
				return []any{"name", p.Name, "new", p.New, "api_key_given", p.APIKey != ""}
			},
			Run: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
				var p RequestParams
				if len(raw) > 0 {
					if err := json.Unmarshal(raw, &p); err != nil {
						return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params must be {\"name\", \"new\", \"api_key\"}")
					}
				}
				id, err := m.identityService()
				if err != nil {
					return nil, err
				}
				res, err := id.RequestToken(ctx, identity.RequestOptions{APIKey: p.APIKey, Name: p.Name, New: p.New})
				return res, toRPC(err)
			},
		},
		"enroll.claim": {
			Timeout: MaxClaimWait + time.Minute,
			Audit: func(raw json.RawMessage) []any {
				var p ClaimParams
				_ = json.Unmarshal(raw, &p)
				return []any{"wait_seconds", p.WaitSeconds}
			},
			Run: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
				var p ClaimParams
				if len(raw) > 0 {
					if err := json.Unmarshal(raw, &p); err != nil || p.WaitSeconds < 0 {
						return nil, localrpc.Errorf(localrpc.CodeBadRequest, "params must be {\"wait_seconds\": <0 or more>}")
					}
				}
				id, err := m.identityService()
				if err != nil {
					return nil, err
				}
				wait := min(time.Duration(p.WaitSeconds)*time.Second, MaxClaimWait)
				res, err := id.Claim(ctx, wait)
				return res, toRPC(err)
			},
		},
		"test": {
			Timeout: time.Minute,
			Run: func(ctx context.Context, _ localrpc.Peer, _ json.RawMessage) (any, error) {
				id, err := m.identityService()
				if err != nil {
					return nil, err
				}
				res, err := id.Test(ctx)
				return res, toRPC(err)
			},
		},
		"config.reload": {
			Timeout: 10 * time.Second,
			Run: func(ctx context.Context, _ localrpc.Peer, _ json.RawMessage) (any, error) {
				id, err := m.identityService()
				if err != nil {
					return nil, err
				}
				st := id.Reload(ctx)
				return map[string]string{"state": st.State, "server": st.Server}, nil
			},
		},
	}
}

func (m *SocketModule) identityService() (*identity.Service, error) {
	if m.identity == nil {
		return nil, localrpc.Errorf(localrpc.CodeUnavailable, "this agent has no identity service")
	}
	return m.identity, nil
}

// toRPC passes an identity error's code (and Retry-After) on to the CLI.
func toRPC(err error) error {
	var e *identity.Error
	if errors.As(err, &e) {
		return &localrpc.Error{Code: e.Code, Message: e.Message, RetryAfter: int(e.RetryAfter.Seconds())}
	}
	return err
}
