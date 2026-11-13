// Package envelope defines the versioned event envelope every data source shares
// (contracts/data-plane-v1.md) and turns untrusted client messages into events.
package envelope

import (
	"bytes"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/identity"
	"meteorcloud/dataplane/internal/streams"
)

// SchemaVersion is the envelope version this service writes.
const SchemaVersion = 1

const (
	KindTelemetry = "telemetry"
	KindEvent     = "event"
	KindInference = "inference"
	// KindLifecycle is emitted by the platform only (stream "meteor.lifecycle").
	KindLifecycle = "lifecycle"
)

// Event is the stored and delivered envelope.
type Event struct {
	ID             string          `json:"id"`
	SchemaVersion  int             `json:"schema_version"`
	Kind           string          `json:"kind"`
	Type           string          `json:"type"`
	Stream         string          `json:"stream"`
	OrganizationID string          `json:"organization_id"`
	ApplicationID  string          `json:"application_id,omitempty"`
	Application    string          `json:"application,omitempty"`
	DeviceID       string          `json:"device_id,omitempty"`
	Environment    string          `json:"environment,omitempty"`
	Time           time.Time       `json:"time"`
	ReceivedAt     time.Time       `json:"received_at"`
	Seq            *int64          `json:"seq,omitempty"`
	Data           json.RawMessage `json:"data"`
}

// Message is what a client sends. Identity fields are deliberately absent: they come
// from the authenticated credential.
type Message struct {
	ID     string          `json:"id,omitempty"`
	Kind   string          `json:"kind,omitempty"`
	Type   string          `json:"type,omitempty"`
	Stream string          `json:"stream"`
	Time   *time.Time      `json:"time,omitempty"`
	Seq    *int64          `json:"seq,omitempty"`
	Data   json.RawMessage `json:"data"`
}

// Rejection explains why a message was not accepted. Codes are part of the contract.
type Rejection struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (r *Rejection) Error() string { return r.Code + ": " + r.Message }

const (
	CodeInvalidStream    = "invalid_stream"
	CodeInvalidKind      = "invalid_kind"
	CodeInvalidID        = "invalid_id"
	CodeInvalidPayload   = "invalid_payload"
	CodePayloadTooLarge  = "payload_too_large"
	CodeInvalidTimestamp = "invalid_timestamp"
	CodeForbiddenStream  = "forbidden_stream"
)

// Limits bound what a single message may carry.
type Limits struct {
	MaxPayloadBytes int
	MaxFutureSkew   time.Duration
	MaxAge          time.Duration
}

// DefaultLimits are used when configuration does not override them.
func DefaultLimits() Limits {
	return Limits{MaxPayloadBytes: 64 << 10, MaxFutureSkew: 5 * time.Minute, MaxAge: 30 * 24 * time.Hour}
}

// ValidID reports whether id is an acceptable event id.
func ValidID(id string) bool {
	if len(id) == 0 || len(id) > 64 {
		return false
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '-', c == '_', c == '.', c == ':':
		default:
			return false
		}
	}
	return true
}

// NewID returns a time-ordered unique event id.
func NewID() string {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.NewString()
	}
	return id.String()
}

// Normalize validates a client message, checks the publish permission and builds the
// envelope with identity fields taken from the credential.
func Normalize(msg *Message, id *identity.Identity, now time.Time, lim Limits) (*Event, *Rejection) {
	if err := streams.ValidateName(msg.Stream); err != nil {
		return nil, &Rejection{CodeInvalidStream, err.Error()}
	}
	kind := msg.Kind
	if kind == "" {
		kind = KindTelemetry
	}
	if kind != KindTelemetry && kind != KindEvent && kind != KindInference {
		return nil, &Rejection{CodeInvalidKind, "kind must be telemetry, event or inference"}
	}
	if !id.CanPublish(msg.Stream) {
		return nil, &Rejection{CodeForbiddenStream, fmt.Sprintf("this application may not publish to stream %q", msg.Stream)}
	}
	eventID := msg.ID
	if eventID == "" {
		eventID = NewID()
	} else if !ValidID(eventID) {
		return nil, &Rejection{CodeInvalidID, "id must be 1-64 characters of A-Z a-z 0-9 . _ : -"}
	}
	if len(msg.Data) > lim.MaxPayloadBytes {
		return nil, &Rejection{CodePayloadTooLarge, fmt.Sprintf("data is larger than %d bytes", lim.MaxPayloadBytes)}
	}
	data := bytes.TrimSpace(msg.Data)
	if len(data) == 0 || data[0] != '{' || !json.Valid(data) {
		return nil, &Rejection{CodeInvalidPayload, "data must be a JSON object"}
	}
	// No store can hold a NUL character inside a JSON text; one such message must not
	// fail the batch it travels in.
	if hasEscapedNUL(data) {
		return nil, &Rejection{CodeInvalidPayload, "data must not contain the character \\u0000"}
	}
	ts := now
	if msg.Time != nil {
		ts = msg.Time.UTC()
		if ts.After(now.Add(lim.MaxFutureSkew)) {
			return nil, &Rejection{CodeInvalidTimestamp, "time is too far in the future"}
		}
		if lim.MaxAge > 0 && ts.Before(now.Add(-lim.MaxAge)) {
			return nil, &Rejection{CodeInvalidTimestamp, "time is older than the accepted window"}
		}
	}
	typ := msg.Type
	if typ == "" {
		typ = msg.Stream
	} else if len(typ) > 128 {
		return nil, &Rejection{CodeInvalidKind, "type is longer than 128 characters"}
	}
	return &Event{
		ID:             eventID,
		SchemaVersion:  SchemaVersion,
		Kind:           kind,
		Type:           typ,
		Stream:         msg.Stream,
		OrganizationID: id.OrganizationID,
		ApplicationID:  id.ApplicationID,
		Application:    id.Application,
		DeviceID:       id.DeviceID,
		Environment:    id.Environment,
		Time:           ts,
		ReceivedAt:     now.UTC(),
		Seq:            msg.Seq,
		Data:           json.RawMessage(data),
	}, nil
}

// hasEscapedNUL reports whether valid JSON contains the escape \u0000 (a backslash
// that is itself escaped does not count).
func hasEscapedNUL(data []byte) bool {
	for i := 0; i+5 < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		if bytes.HasPrefix(data[i+1:], []byte("u0000")) {
			return true
		}
		i++ // skip the escaped character
	}
	return false
}
