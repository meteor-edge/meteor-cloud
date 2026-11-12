// Package mqttgw ingests what applications and devices publish directly over MQTT.
//
// MQTT gives the sender no error channel, so a message that cannot be accepted is
// written to the dead-letter table with a reason instead of being dropped.
package mqttgw

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"time"

	"meteorcloud/dataplane/internal/bus"
	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/ingest"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

// Submitter is the ingestion pipeline.
type Submitter interface {
	Submit(ctx context.Context, events []*envelope.Event) ([]string, error)
}

// Ingress implements bus.IngressHandler.
type Ingress struct {
	Policy   *identity.Store
	Pipeline Submitter
	Commands *commands.Service
	Store    storage.Store
	Metrics  *observability.Metrics
	Log      *slog.Logger
	Limits   envelope.Limits
	// MaxWait bounds how long one message is retried while storage is unavailable.
	MaxWait time.Duration
}

type mqttMessage struct {
	ID   string          `json:"id"`
	Type string          `json:"type"`
	Time *time.Time      `json:"time"`
	Seq  *int64          `json:"seq"`
	Data json.RawMessage `json:"data"`
}

// deadLetter records a message that cannot be processed. It reports false when the
// record could not be written: the message is then not acknowledged to the broker.
func (g *Ingress) deadLetter(ctx context.Context, t bus.Topic, reason, detail string, payload []byte) bool {
	g.Metrics.DeadLetters.WithLabelValues(reason).Inc()
	if len(payload) > g.Limits.MaxPayloadBytes+1024 {
		payload = payload[:g.Limits.MaxPayloadBytes+1024]
	}
	err := g.Store.AddDeadLetter(ctx, storage.DeadLetter{ReceivedAt: time.Now().UTC(), Source: "mqtt",
		OrganizationID: t.OrgID, Reason: reason, Detail: detail, Payload: payload})
	if err != nil {
		g.Metrics.StorageErrors.WithLabelValues("dead_letter").Inc()
		// Last resort: the loss is at least visible. Never log the payload.
		g.Log.Error("dead-letter write failed; the message is left unacknowledged for redelivery", "reason", reason,
			"organization_id", t.OrgID, "stream", t.Stream, "error", err.Error())
		return false
	}
	return true
}

// identityFor rebuilds the publisher's identity from the topic. The broker ACL
// already restricted the topic to the publisher's own organization, application and
// device; permissions are re-checked against the current policy.
func (g *Ingress) identityFor(t bus.Topic) (*identity.Identity, bool) {
	app, ok := g.Policy.ApplicationBySlug(t.OrgID, t.App)
	if !ok {
		return nil, false
	}
	return &identity.Identity{OrganizationID: t.OrgID, ApplicationID: app.ID, Application: app.Slug,
		DeviceID: t.Device, Permissions: app.Permissions}, true
}

// Message stores one client message, or dead-letters it.
func (g *Ingress) Message(ctx context.Context, t bus.Topic, payload []byte) bool {
	g.Metrics.MessagesReceived.WithLabelValues("mqtt").Inc()
	reject := func(reason, detail string) bool {
		g.Metrics.MessagesRejected.WithLabelValues("mqtt", reason).Inc()
		return g.deadLetter(ctx, t, reason, detail, payload)
	}
	id, ok := g.identityFor(t)
	if !ok {
		return reject("unknown_application", "no enabled application "+t.App)
	}
	if len(payload) > g.Limits.MaxPayloadBytes+1024 {
		return reject(envelope.CodePayloadTooLarge, "message is larger than the payload limit")
	}
	var msg mqttMessage
	if err := json.Unmarshal(payload, &msg); err != nil {
		return reject(envelope.CodeInvalidPayload, "message is not a JSON object with a data field")
	}
	ev, rej := envelope.Normalize(&envelope.Message{ID: msg.ID, Kind: t.Kind, Type: msg.Type, Stream: t.Stream,
		Time: msg.Time, Seq: msg.Seq, Data: msg.Data}, id, time.Now(), g.Limits)
	if rej != nil {
		return reject(rej.Code, rej.Message)
	}
	// Storage or queue trouble: hold the message (the broker is not acknowledged yet,
	// which is the backpressure) and retry with backoff.
	maxWait := g.MaxWait
	if maxWait <= 0 {
		maxWait = 2 * time.Minute
	}
	deadline := time.Now().Add(maxWait)
	backoff := 100 * time.Millisecond
	for {
		sctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		_, err := g.Pipeline.Submit(sctx, []*envelope.Event{ev})
		cancel()
		if err == nil {
			return true
		}
		if ctx.Err() != nil || errors.Is(err, ingest.ErrStopped) {
			// Shutting down: not acknowledged to the broker, so it is redelivered.
			return false
		}
		if time.Now().After(deadline) {
			return g.deadLetter(ctx, t, "storage_unavailable", "event "+ev.ID+" could not be stored: "+err.Error(), payload)
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(backoff):
		}
		if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

type mqttAck struct {
	CommandID string          `json:"command_id"`
	Status    string          `json:"status"`
	Result    json.RawMessage `json:"result"`
	Error     string          `json:"error"`
}

// CommandAck applies a command acknowledgement published over MQTT.
func (g *Ingress) CommandAck(ctx context.Context, t bus.Topic, payload []byte) bool {
	id, ok := g.identityFor(t)
	if !ok {
		return g.deadLetter(ctx, t, "unknown_application", "command acknowledgement from "+t.App, payload)
	}
	var ack mqttAck
	if err := json.Unmarshal(payload, &ack); err != nil || ack.CommandID == "" {
		return g.deadLetter(ctx, t, "invalid_ack", "acknowledgement needs command_id and status", payload)
	}
	if _, err := g.Commands.Ack(ctx, id, ack.CommandID, ack.Status, ack.Result, ack.Error); err != nil {
		var invalid *commands.InvalidError
		if errors.Is(err, commands.ErrForbidden) || errors.Is(err, storage.ErrNotFound) || errors.As(err, &invalid) {
			return g.deadLetter(ctx, t, "rejected_ack", err.Error(), payload)
		}
		// The command store is in trouble: leave the acknowledgement with the broker.
		g.Metrics.StorageErrors.WithLabelValues("command_ack").Inc()
		g.Log.Warn("command acknowledgement not applied; left for redelivery", "error", err.Error())
		return false
	}
	return true
}
