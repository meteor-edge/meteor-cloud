package dispatch

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

// ControlPlane calls the control plane's internal rule routes (/internal/rules/...).
type ControlPlane struct {
	URL    string
	Token  string
	Client *http.Client
}

// Post sends body as JSON and decodes a 2xx answer into out. A 4xx other than 408
// and 429 is permanent: the same request will be refused again.
func (c *ControlPlane) Post(ctx context.Context, path string, body, out any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return Permanent(err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(c.URL, "/")+path, bytes.NewReader(raw))
	if err != nil {
		return Permanent(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Data-Plane-Internal-Token", c.Token)
	client := c.Client
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if err := statusError("control plane "+path, resp, true); err != nil {
		return err
	}
	if out == nil {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
		return nil
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(out); err != nil {
		return fmt.Errorf("decode %s answer: %w", path, err)
	}
	return nil
}

// statusError is nil for a 2xx answer. withBody adds the start of the answer.
func statusError(what string, resp *http.Response, withBody bool) error {
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	err := fmt.Errorf("%s answered HTTP %d", what, resp.StatusCode)
	if withBody {
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		err = fmt.Errorf("%w: %s", err, oneLine(string(snippet)))
	}
	if resp.StatusCode >= 400 && resp.StatusCode < 500 && resp.StatusCode != http.StatusRequestTimeout &&
		resp.StatusCode != http.StatusTooManyRequests {
		return Permanent(err)
	}
	return err
}

// envelope is the outbox payload plus its idempotency key, as the control plane takes it.
func envelope(item state.OutboxItem) (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(item.Payload, &m); err != nil {
		return nil, Permanent(err)
	}
	m["idempotency_key"] = item.Key
	return m, nil
}

// AlertSender records alert transitions in the control plane.
type AlertSender struct{ CP *ControlPlane }

func (s *AlertSender) Send(ctx context.Context, item state.OutboxItem, _ *engine.ActionPayload) (Outcome, error) {
	m, err := envelope(item)
	if err != nil {
		return "", err
	}
	var out struct {
		Results []struct {
			Status string `json:"status"`
			Detail string `json:"detail"`
		} `json:"results"`
	}
	if err := s.CP.Post(ctx, "/internal/rules/alert-transitions", map[string]any{"items": []any{m}}, &out); err != nil {
		return "", err
	}
	if len(out.Results) == 1 && out.Results[0].Status == "ignored" {
		return Skipped, nil
	}
	return Delivered, nil
}

// Alert records of one organization go in batches; each is applied on its own.
func (s *AlertSender) Groups(context.Context, state.OutboxItem, *engine.ActionPayload) (ok, anyRule bool, limit int) {
	return true, true, 100
}

func (s *AlertSender) SendGroup(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) []Result {
	results := make([]Result, len(items))
	var body []any
	for _, it := range items {
		m, err := envelope(it)
		if err != nil {
			return s.each(ctx, items, ps)
		}
		body = append(body, m)
	}
	var out struct {
		Results []struct {
			Status string `json:"status"`
		} `json:"results"`
	}
	err := s.CP.Post(ctx, "/internal/rules/alert-transitions", map[string]any{"items": body}, &out)
	if IsPermanent(err) || (err == nil && len(out.Results) != len(items)) {
		// One refused item must not take the others with it.
		return s.each(ctx, items, ps)
	}
	for i := range results {
		results[i] = Result{Delivered, err}
		if err == nil && out.Results[i].Status == "ignored" {
			results[i].Outcome = Skipped
		}
	}
	return results
}

func (s *AlertSender) each(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) []Result {
	results := make([]Result, len(items))
	for i := range items {
		out, err := s.Send(ctx, items[i], ps[i])
		results[i] = Result{out, err}
	}
	return results
}

// CommandSender asks the control plane to send a device command. The control plane
// re-checks, at delivery time, that the rule's author may still send it to that
// device, with the same permission and device-group scope as the console.
type CommandSender struct{ CP *ControlPlane }

func (s *CommandSender) Send(ctx context.Context, item state.OutboxItem, _ *engine.ActionPayload) (Outcome, error) {
	m, err := envelope(item)
	if err != nil {
		return "", err
	}
	var out struct {
		Status string `json:"status"`
	}
	if err := s.CP.Post(ctx, "/internal/rules/commands", m, &out); err != nil {
		return "", err
	}
	if out.Status == "skipped" {
		return Skipped, nil
	}
	return Delivered, nil
}

// PlatformEventSender records a rule event in the control plane (audit log).
type PlatformEventSender struct{ CP *ControlPlane }

func (s *PlatformEventSender) Send(ctx context.Context, item state.OutboxItem, _ *engine.ActionPayload) (Outcome, error) {
	m, err := envelope(item)
	if err != nil {
		return "", err
	}
	if err := s.CP.Post(ctx, "/internal/rules/platform-events", m, nil); err != nil {
		return "", err
	}
	return Delivered, nil
}
