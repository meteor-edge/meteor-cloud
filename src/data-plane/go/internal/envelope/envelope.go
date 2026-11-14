// Package envelope defines the versioned event envelope every data source shares
// (contracts/data-plane-v1.md) and turns untrusted client messages into events.
package envelope

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

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
	data, rej := CanonicalData(msg.Data, lim.MaxPayloadBytes)
	if rej != nil {
		return nil, rej
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
	} else if strings.ContainsRune(typ, 0) || !utf8.ValidString(typ) {
		return nil, &Rejection{CodeInvalidKind, "type must be valid text without the character \\u0000"}
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
		Data:           data,
	}, nil
}

// CanonicalData checks that data is a single JSON object that ClickHouse (and
// PostgreSQL) can store, and returns a compact form. Invalid payloads are rejected
// here so one bad message never fails a whole insert batch.
//
// Energy readings and similar telemetry are free-form objects (any keys/numbers);
// only shape and storability are enforced — not a fixed schema.
func CanonicalData(data []byte, maxBytes int) (json.RawMessage, *Rejection) {
	data = bytes.TrimSpace(data)
	if len(data) == 0 || data[0] != '{' {
		return nil, &Rejection{CodeInvalidPayload, "data must be a JSON object"}
	}
	if maxBytes > 0 && len(data) > maxBytes {
		return nil, &Rejection{CodePayloadTooLarge, fmt.Sprintf("data is larger than %d bytes", maxBytes)}
	}
	if !json.Valid(data) {
		return nil, &Rejection{CodeInvalidPayload, "data must be a JSON object"}
	}
	// No store can hold a NUL character inside a JSON text; one such message must not
	// fail the batch it travels in.
	if hasUnstorableEscape(data) {
		return nil, &Rejection{CodeInvalidPayload, "data must not contain \\u0000 or an unpaired surrogate escape"}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	var obj map[string]json.RawMessage
	if err := dec.Decode(&obj); err != nil {
		return nil, &Rejection{CodeInvalidPayload, "data must be a JSON object"}
	}
	if dec.More() {
		return nil, &Rejection{CodeInvalidPayload, "data must be a single JSON object"}
	}
	var encoded bytes.Buffer
	enc := json.NewEncoder(&encoded)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(obj); err != nil {
		return nil, &Rejection{CodeInvalidPayload, "data could not be re-encoded"}
	}
	var compact bytes.Buffer
	if err := json.Compact(&compact, bytes.TrimSpace(encoded.Bytes())); err != nil {
		return nil, &Rejection{CodeInvalidPayload, "data could not be re-encoded"}
	}
	if maxBytes > 0 && compact.Len() > maxBytes {
		return nil, &Rejection{CodePayloadTooLarge, fmt.Sprintf("data is larger than %d bytes", maxBytes)}
	}
	return json.RawMessage(compact.Bytes()), nil
}

// hasUnstorableEscape reports whether valid JSON contains an escape that no store
// accepts inside a JSON text: \u0000, or a surrogate that is not half of a pair
// (a backslash that is itself escaped does not count).
func hasUnstorableEscape(data []byte) bool {
	hex := func(i int) (rune, bool) {
		if i+6 > len(data) || data[i] != '\\' || data[i+1] != 'u' {
			return 0, false
		}
		v, err := strconv.ParseUint(string(data[i+2:i+6]), 16, 16)
		return rune(v), err == nil
	}
	for i := 0; i+1 < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		r, ok := hex(i)
		if !ok {
			i++ // skip the escaped character
			continue
		}
		switch {
		case r == 0:
			return true
		case r >= 0xD800 && r <= 0xDBFF: // high surrogate: a low one must follow
			low, ok := hex(i + 6)
			if !ok || low < 0xDC00 || low > 0xDFFF {
				return true
			}
			i += 11
		case r >= 0xDC00 && r <= 0xDFFF: // low surrogate without a high one
			return true
		default:
			i += 5
		}
	}
	return false
}
