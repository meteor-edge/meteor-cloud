package dispatch

import (
	"context"
	"net/netip"

	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

type resolverFunc func() (*Delivery, error)

func (f resolverFunc) Resolve(context.Context, state.OutboxItem, *engine.ActionPayload) (*Delivery, error) {
	return f()
}

func mustAddr(s string) netip.Addr { return netip.MustParseAddr(s) }
