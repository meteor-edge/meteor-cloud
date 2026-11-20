package api

import (
	"context"
	"net/http"
)

// Action is one one-off action from GET /actions (actions.response.json). It
// may hold a password: never log it. Fields a kind does not use are empty.
type Action struct {
	ID            string  `json:"id"`
	Kind          string  `json:"kind"`
	Username      *string `json:"username"`
	Password      *string `json:"password"`
	Sudo          bool    `json:"sudo"`
	AuthorizedKey *string `json:"authorized_key"`
	FromVersion   *string `json:"from_version"`
}

// Action result states.
const (
	ActionDone   = "done"
	ActionFailed = "failed"
)

// MaxActionResults is the contract's limit of results in one report.
const MaxActionResults = 50

// ActionResult says what happened to one action (actions-report.request.json).
// Error is at most 500 characters.
type ActionResult struct {
	ID    string `json:"id"`
	State string `json:"state"`
	Error string `json:"error,omitempty"`
}

// Actions collects the actions waiting for this device. The server hands each
// one out once, so the caller must report every one it got.
func (c *Client) Actions(ctx context.Context, deviceToken string) ([]Action, error) {
	var out struct {
		Actions []Action `json:"actions"`
	}
	if err := c.do(ctx, http.MethodGet, "/api/v1/agent/actions", deviceToken, nil, &out); err != nil {
		return nil, err
	}
	return out.Actions, nil
}

// ReportActions sends results (at most MaxActionResults) and returns how many
// the server stored.
func (c *Client) ReportActions(ctx context.Context, deviceToken string, results []ActionResult) (int, error) {
	var out struct {
		Accepted int `json:"accepted"`
	}
	body := map[string]any{"results": results}
	if err := c.do(ctx, http.MethodPost, "/api/v1/agent/actions/report", deviceToken, body, &out); err != nil {
		return 0, err
	}
	return out.Accepted, nil
}
