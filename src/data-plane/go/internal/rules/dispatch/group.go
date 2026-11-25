package dispatch

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

const (
	maxGroup  = 1000 // devices in one message
	maxListed = 20   // devices named in it
)

// GroupResolver looks up an integration once for several devices of one rule.
type GroupResolver interface {
	ResolveGroup(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) (*Delivery, []DeviceDelivery, error)
}

// DeviceDelivery is what differs between the devices of a group.
type DeviceDelivery struct {
	Name     string `json:"name"`
	Silenced bool   `json:"silenced"`
}

// ResolveGroup answers one DeviceDelivery per item.
func (r *ControlPlaneResolver) ResolveGroup(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) (*Delivery, []DeviceDelivery, error) {
	devices := make([]map[string]any, len(items))
	for i, it := range items {
		devices[i] = map[string]any{"device_id": it.DeviceID, "at": ps[i].Transition.At}
	}
	var d struct {
		Delivery
		Devices []DeviceDelivery `json:"devices"`
	}
	err := r.CP.Post(ctx, "/internal/rules/deliveries", map[string]any{
		"integration_id": ps[0].Action.IntegrationID, "organization_id": items[0].OrgID, "rule_id": items[0].RuleID,
		"device_id": items[0].DeviceID, "at": ps[0].Transition.At, "devices": devices,
	}, &d)
	if err == nil && len(d.Devices) != len(items) {
		err = fmt.Errorf("control plane answered %d devices for %d", len(d.Devices), len(items))
	}
	return &d.Delivery, d.Devices, err
}

// Messages to people are grouped; webhooks are for programs and stay one per alert.
func (s *WebhookSender) Groups(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (ok, anyRule bool, limit int) {
	if _, can := s.Resolver.(GroupResolver); !can {
		return false, false, 0
	}
	d, err := s.Resolver.Resolve(ctx, item, p)
	if err != nil {
		return false, false, 0 // Send reports it
	}
	switch d.Kind {
	case "email", "telegram", "slack":
		return true, false, maxGroup
	}
	return false, false, 0
}

// SendGroup sends one message for all devices that are not silenced.
func (s *WebhookSender) SendGroup(ctx context.Context, items []state.OutboxItem, ps []*engine.ActionPayload) []Result {
	results := make([]Result, len(items))
	fail := func(err error) []Result {
		for i := range results {
			results[i] = Result{Err: err}
		}
		return results
	}
	gr, ok := s.Resolver.(GroupResolver)
	if !ok {
		return fail(Permanent(errors.New("grouped delivery is not available")))
	}
	d, devices, err := gr.ResolveGroup(ctx, items, ps)
	if err != nil {
		return fail(err)
	}
	var sent []int
	for i := range items {
		if !d.Enabled || devices[i].Silenced {
			results[i].Outcome = Skipped
			continue
		}
		sent = append(sent, i)
	}
	if len(sent) == 0 {
		return results
	}
	first := sent[0]
	p := ps[first]
	d.DeviceName = devices[first].Name
	if len(sent) > 1 {
		d.Group = groupLines(sent, ps, devices)
	}
	out, err := s.Deliver(ctx, d, items[first], p)
	for _, i := range sent {
		results[i] = Result{out, err}
	}
	return results
}

// groupLines is one line per device, with its readings.
func groupLines(sent []int, ps []*engine.ActionPayload, devices []DeviceDelivery) []string {
	seen := map[string]bool{}
	var out []string
	for _, i := range sent {
		if seen[ps[i].DeviceID] {
			continue // the same device twice, such as two actions on one channel
		}
		seen[ps[i].DeviceID] = true
		line := oneLine(devices[i].Name)
		if line == "" {
			line = "device " + ps[i].DeviceID
		}
		if v := readings(ps[i].Transition.Values); len(v) > 0 {
			line += " (" + strings.Join(v, ", ") + ")"
		}
		if r := []rune(line); len(r) > 120 {
			line = string(r[:120]) + "…" // chat messages have a length limit
		}
		out = append(out, line)
	}
	return out
}
