// Package bus is the message transport between data-plane replicas and towards
// subscribers. EMQX (MQTT 5) is the first implementation; the interface keeps Kafka
// or another transport possible without touching ingestion, live delivery or commands.
package bus

import (
	"context"
	"sync"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/storage"
)

// Handlers receive what the transport delivers. They must not block for long.
type Handlers struct {
	// OnLive is called for every stored event, on every replica.
	OnLive func(ev *envelope.Event)
	// OnCommand is called for every dispatched command, on every replica.
	OnCommand func(cmd *storage.Command)
}

// Bus distributes stored events and dispatched commands.
type Bus interface {
	Start(ctx context.Context, h Handlers) error
	PublishLive(ctx context.Context, ev *envelope.Event) error
	PublishCommand(ctx context.Context, cmd *storage.Command) error
	Ready() bool
	Close()
}

// Memory delivers in process: single-replica deployments without a broker, and tests.
type Memory struct {
	mu sync.RWMutex
	h  Handlers
	// Fail makes publishes return this error (tests).
	Fail error
}

func NewMemory() *Memory { return &Memory{} }

func (m *Memory) Start(_ context.Context, h Handlers) error {
	m.mu.Lock()
	m.h = h
	m.mu.Unlock()
	return nil
}

func (m *Memory) PublishLive(_ context.Context, ev *envelope.Event) error {
	m.mu.RLock()
	h, fail := m.h.OnLive, m.Fail
	m.mu.RUnlock()
	if fail != nil {
		return fail
	}
	if h != nil {
		h(ev)
	}
	return nil
}

func (m *Memory) PublishCommand(_ context.Context, cmd *storage.Command) error {
	m.mu.RLock()
	h, fail := m.h.OnCommand, m.Fail
	m.mu.RUnlock()
	if fail != nil {
		return fail
	}
	if h != nil {
		h(cmd)
	}
	return nil
}

func (m *Memory) Ready() bool { return true }
func (m *Memory) Close()      {}
