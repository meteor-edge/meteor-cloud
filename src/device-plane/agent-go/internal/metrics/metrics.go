// Package metrics is the module that publishes the device's readings on
// devices/{id}/metrics every 60 s while MQTT is connected, and the changes it
// saw in between on devices/{id}/events: app lifecycle (Docker containers,
// read through the root helper) and journal warnings. It ports the Python
// agent's metrics loop and observer. Every reading is best effort: a missing
// tool or file leaves the key out, and one broken reading never stops the
// others. Optional: a crash loop here parks this module, nothing else.
package metrics

import (
	"context"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/telemetry"
)

// name is the module's id.
const name = "metrics"

// DefaultInterval is the contract's metrics interval.
const DefaultInterval = 60 * time.Second

// IntervalEnv overrides the interval in seconds, for the contract suite and
// development only; devices use DefaultInterval.
const IntervalEnv = "METEORCLI_METRICS_INTERVAL"

// maxLogEvents bounds the journal warnings sent per interval.
const maxLogEvents = 200

// maxPending bounds the events kept for the next round while MQTT is down;
// the oldest go first.
const maxPending = 500

// collectTimeout bounds one round of readings (helper calls included).
const collectTimeout = 45 * time.Second

// caller is the part of the helper client this module uses.
type caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// Module publishes metrics and events.
type Module struct {
	// Interval between metrics; zero means IntervalEnv or DefaultInterval.
	Interval time.Duration

	// helper replaces Deps.Helper (tests).
	helper caller
}

// Name implements agent.Module.
func (m *Module) Name() string { return name }

// Critical implements agent.Module.
func (m *Module) Critical() bool { return false }

// Run implements agent.Module: wait until connected, publish, wait the
// interval (or the next connect), repeat. The observer's state (the last look
// at the apps, the journal cursor) lives as long as Run.
func (m *Module) Run(ctx context.Context, d agent.Deps) error {
	o := newObserver(d)
	if m.helper != nil {
		o.helper, o.apps = m.helper, newApps(m.helper)
		o.apps.composeDir = composeDir(d)
	}
	every := m.interval()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-d.Bus.Up():
		}
		d.Health.Tick(name)
		o.publish(ctx)
		t := time.NewTimer(every)
		select {
		case <-ctx.Done():
			t.Stop()
			return nil
		case <-t.C:
		}
	}
}

func (m *Module) interval() time.Duration {
	if m.Interval > 0 {
		return m.Interval
	}
	if v, err := strconv.ParseFloat(os.Getenv(IntervalEnv), 64); err == nil && v > 0 {
		return time.Duration(v * float64(time.Second))
	}
	return DefaultInterval
}

// observer holds what one Run remembers between looks.
type observer struct {
	d      agent.Deps
	helper caller
	tools  *telemetry.Tools
	apps   *apps
	now    func() time.Time
	// network is the last counter reading, for rates.
	netAt          time.Time
	netRx, netTx   uint64
	cursor         string
	warningsFailed bool
	// pending are events collected but not published yet (the session was
	// down), sent first in the next round.
	pending []map[string]any
	// unsent is the last metrics body that could not be published; a newer
	// failure replaces it, so a long outage keeps one body, not a backlog.
	unsent map[string]any
	// deviceID is the identity that produced pending/unsent; a change clears them.
	deviceID string
}

func newObserver(d agent.Deps) *observer {
	var h caller = d.Helper
	if d.Helper == nil {
		h = noHelper{}
	}
	o := &observer{d: d, helper: h, tools: &telemetry.Tools{Host: d.Host}, apps: newApps(h), now: time.Now}
	o.apps.composeDir = composeDir(d)
	return o
}

// composeDir is the resolved compose directory, as Docker's labels show it.
func composeDir(d agent.Deps) string {
	dir := d.Paths.ComposeDir()
	if real, err := filepath.EvalSymlinks(dir); err == nil {
		return real
	}
	return dir
}

// publish collects one round and publishes the metrics, then the events.
// Events that cannot be sent wait for the next round (the journal cursor and
// the app baseline have moved on, so they would be lost otherwise). Of the
// metrics only the latest unsent body waits: a session restarted right after
// connecting (new credentials) must not lose the first round.
func (o *observer) publish(ctx context.Context) {
	o.dropBuffersIfIdentityChanged()
	cctx, cancel := context.WithTimeout(ctx, collectTimeout)
	body, events := o.collect(cctx)
	cancel()
	stamp := bus.Timestamp(o.now())
	for _, e := range events {
		if _, ok := e["timestamp"]; !ok {
			e["timestamp"] = stamp
		}
	}
	o.pending = append(o.pending, events...)
	if n := len(o.pending) - maxPending; n > 0 {
		o.d.Log.Warn("MQTT down too long; dropped the oldest events", "dropped", n)
		o.pending = o.pending[n:]
	}
	if o.unsent != nil {
		if !o.send(ctx, o.unsent) {
			o.unsent = body
			return
		}
		o.unsent = nil
	}
	if !o.send(ctx, body) {
		o.unsent = body
		return
	}
	for len(o.pending) > 0 {
		e := o.pending[0]
		if err := o.d.Bus.PublishJSON(ctx, bus.Events, e, false); err != nil {
			if !errors.Is(err, bus.ErrNotConnected) && ctx.Err() == nil {
				o.d.Log.Warn("could not publish an event; kept for the next round", "type", e["type"], "err", err)
			}
			return
		}
		o.pending = o.pending[1:]
	}
	o.pending = nil // let the backing array go
}

// dropBuffersIfIdentityChanged discards unsent metrics and events when the
// device id changes, so telemetry buffered under one identity is never
// published under another.
func (o *observer) dropBuffersIfIdentityChanged() {
	id := ""
	if o.d.Identity != nil {
		id = o.d.Identity.DeviceID()
	}
	if o.deviceID != id {
		if o.deviceID != "" {
			o.pending, o.unsent = nil, nil
		}
		o.deviceID = id
	}
}

// send publishes one metrics body and says whether it went out.
func (o *observer) send(ctx context.Context, body map[string]any) bool {
	err := o.d.Bus.PublishJSON(ctx, bus.Metrics, body, false)
	if err != nil && !errors.Is(err, bus.ErrNotConnected) && ctx.Err() == nil {
		o.d.Log.Warn("could not publish metrics", "err", err)
	}
	return err == nil
}

// collect returns the metrics body and the events of this round.
func (o *observer) collect(ctx context.Context) (map[string]any, []map[string]any) {
	body := telemetry.Metrics(ctx, o.d.Host)
	for k, v := range o.networkRates() {
		body[k] = v
	}
	if dbm, ok := o.d.Host.WifiSignalDBm(); ok {
		body["wifi_signal_dbm"] = dbm
	}
	var events []map[string]any
	if o.tools.Get(ctx).Docker {
		appMetrics, appEvents := o.apps.observe(ctx)
		for k, v := range appMetrics {
			body[k] = v
		}
		events = append(events, appEvents...)
	}
	events = append(events, o.warnings(ctx)...)
	if o.d.Errors != nil {
		body["agent_errors"] = o.d.Errors.Take()
	} else {
		body["agent_errors"] = 0
	}
	body["timestamp"] = bus.Timestamp(o.now())
	return body, events
}

// networkRates returns bytes per second since the last look.
func (o *observer) networkRates() map[string]any {
	rx, tx, ok := o.d.Host.NetworkBytes()
	if !ok {
		return nil
	}
	now := o.now()
	prevAt, prevRx, prevTx := o.netAt, o.netRx, o.netTx
	o.netAt, o.netRx, o.netTx = now, rx, tx
	elapsed := now.Sub(prevAt).Seconds()
	if prevAt.IsZero() || elapsed <= 0 || rx < prevRx || tx < prevTx { // first look, or counters reset
		return nil
	}
	rate := func(d uint64) float64 { return math.Round(float64(d)/elapsed*10) / 10 }
	return map[string]any{"net_rx_bps": rate(rx - prevRx), "net_tx_bps": rate(tx - prevTx)}
}

// warnings returns journal entries at warning or worse since the last look, as
// log events (read by the helper: the agent may not read the journal).
func (o *observer) warnings(ctx context.Context) []map[string]any {
	var reply helper.WarningsReply
	if err := o.helper.Call(ctx, "logs.warnings", helper.WarningsRequest{AfterCursor: o.cursor}, &reply); err != nil {
		if !o.warningsFailed {
			o.d.Log.Info("cannot read journal warnings through the helper; will keep trying", "err", err)
			o.warningsFailed = true
		}
		return nil
	}
	o.warningsFailed = false
	var events []map[string]any
	for _, w := range reply.Entries {
		if w.Cursor != "" {
			o.cursor = w.Cursor
		}
		msg := agent.Redact(w.Message)
		e := map[string]any{"type": "log", "message": msg, "priority": w.Priority, "unit": w.Unit, "source": w.Source}
		if w.Timestamp != nil {
			e["timestamp"] = *w.Timestamp
		}
		events = append(events, e)
	}
	if len(events) > maxLogEvents {
		events = events[len(events)-maxLogEvents:]
	}
	return events
}

// noHelper answers every call with an error (tests without a helper).
type noHelper struct{}

func (noHelper) Call(context.Context, string, any, any) error {
	return errors.New("no helper")
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
