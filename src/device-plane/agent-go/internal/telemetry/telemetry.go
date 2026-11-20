// Package telemetry turns platform readings into the payloads of the contract:
// the inventory sent with register, enrollment and every heartbeat
// (common.json#/$defs/inventory), the metrics published on devices/{id}/metrics
// (mqtt/metrics.json), and the tool booleans of capabilities. It is a library,
// not a module: the heartbeat and identity use Inventory, the MQTT bus (a later
// pull request) publishes Metrics every 60 s.
package telemetry

import (
	"context"
	"math"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/platform"
)

// cpuSample is how long Metrics watches /proc/stat for cpu_percent.
const cpuSample = 100 * time.Millisecond

// toolsTTL is how long a tool probe is reused. Probing starts the Docker CLI, so
// the heartbeat must not do it every minute; installing Docker shows up within this.
const toolsTTL = 10 * time.Minute

// Inventory returns every inventory key of the contract, with nil for values
// this host does not have (the Python agent sent the same keys).
func Inventory(h *platform.Host, agentVersion string) api.Inventory {
	osName, osVersion := h.OSRelease()
	cpuModel, cores := h.CPU()
	release, machine := h.Uname()
	hostname, err := h.Hostname()
	if err != nil {
		hostname = ""
	}
	return api.Inventory{
		"serial_number":  orNil(h.SerialNumber()),
		"mac_addresses":  h.MACAddresses(),
		"hostname":       orNil(hostname),
		"os_name":        orNil(osName),
		"os_version":     orNil(osVersion),
		"kernel_version": orNil(release),
		"architecture":   orNil(machine),
		"hardware_model": orNil(h.HardwareModel()),
		"cpu_model":      orNil(cpuModel),
		"cpu_cores":      positiveOrNil(cores),
		"memory_mb":      positiveOrNil(h.MemoryMB()),
		"local_ip":       orNil(h.LocalIPv4()),
		"agent_version":  orNil(agentVersion),
	}
}

// Metrics returns the best-effort readings of mqtt/metrics.json. Missing
// readings are left out, except temperature_c, which is null when unknown
// (as the Python agent sent it). It takes about 100 ms (the CPU sample).
func Metrics(ctx context.Context, h *platform.Host) map[string]any {
	m := map[string]any{}
	if cpu, ok := h.CPUPercent(ctx, cpuSample); ok {
		m["cpu_percent"] = round(cpu, 1)
	}
	if used, total, ok := h.Memory(); ok {
		m["memory_percent"] = round(percent(used, total), 1)
		m["memory_used_bytes"] = used
		m["memory_total_bytes"] = total
	}
	if used, total, ok := h.Disk("/"); ok {
		m["disk_percent"] = round(percent(used, total), 1)
		m["disk_used_bytes"] = used
		m["disk_total_bytes"] = total
	}
	if load, ok := h.LoadAverage(); ok {
		m["load_1m"], m["load_5m"], m["load_15m"] = round(load[0], 2), round(load[1], 2), round(load[2], 2)
	}
	if up, ok := h.Uptime(); ok {
		m["uptime_seconds"] = up
	}
	m["temperature_c"] = nil
	if t, ok := h.TemperatureC(); ok {
		m["temperature_c"] = round(t, 1)
	}
	return m
}

// Tools caches the host's tool probe for toolsTTL. Safe for concurrent use.
type Tools struct {
	Host *platform.Host

	mu  sync.Mutex
	at  time.Time
	got platform.Tools
}

// Get returns the cached probe, probing again when it is older than toolsTTL.
func (t *Tools) Get(ctx context.Context) platform.Tools {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.at.IsZero() || time.Since(t.at) > toolsTTL {
		t.got = t.Host.Tools(ctx)
		t.at = time.Now()
	}
	return t.got
}

func orNil(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func positiveOrNil(n int) any {
	if n <= 0 {
		return nil
	}
	return n
}

func percent(used, total uint64) float64 {
	return min(100, float64(used)*100/float64(total))
}

func round(v float64, digits int) float64 {
	p := math.Pow(10, float64(digits))
	return math.Round(v*p) / p
}
