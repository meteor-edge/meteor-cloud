// Package rules is the entry point of the rule engine for the rest of the data plane.
//
// It is the only rules package that knows the data plane's event envelope: Processor
// turns an envelope.Event into the engine's own eval.Event and hands it to the
// engine. A different event source (another queue, an HTTP feed, a replay tool)
// only needs its own small conversion function; the engine does not change.
//
// Layout:
//
//	dsl     versioned JSON rule definition and validation
//	eval    compiled condition tree, stateful nodes, pure evaluation
//	state   durable records and the Store (memory, PostgreSQL schema "rules")
//	engine  candidate selection, dedup, ordering, alert state machine, timers, outbox
package rules

import (
	"context"
	"fmt"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/eval"
)

// FromEnvelope converts a normalized data-plane event. Organization and device come
// from the envelope, which takes them from the authenticated connection; the payload
// is only ever read as metric fields.
func FromEnvelope(ev *envelope.Event) (*eval.Event, error) {
	if ev == nil {
		return nil, fmt.Errorf("%w: nil event", engine.ErrPermanent)
	}
	fields, err := eval.FlattenJSON(ev.Data)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", engine.ErrPermanent, err)
	}
	return &eval.Event{ID: ev.ID, OrgID: ev.OrganizationID, DeviceID: ev.DeviceID, Kind: ev.Kind,
		Stream: ev.Stream, Time: ev.Time.UTC(), Fields: fields}, nil
}

// Processor feeds envelope events to the engine. Its Handle method matches the rule
// processor consumer's handler: nil acknowledges the event, an error asks for a
// redelivery, and an error wrapping engine.ErrPermanent must terminate the event.
type Processor struct {
	Engine *engine.Engine
}

// Handle converts and evaluates one event.
func (p *Processor) Handle(ctx context.Context, ev *envelope.Event) error {
	if ev != nil && ev.DeviceID == "" {
		return nil // application events without a device are not evaluated yet
	}
	e, err := FromEnvelope(ev)
	if err != nil {
		return err
	}
	return p.Engine.Handle(ctx, e)
}
