package workloads

import (
	"context"
	"maps"
	"slices"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/deploy"
	"meteorcloud/meteorcli/internal/helper"
)

// Disk is Docker's disk space on the device.
type Disk struct {
	TakenAt string           `json:"taken_at"`
	Items   []helper.DiskUse `json:"items"`
}

// How old the disk space may be: measuring volumes reads every file in them.
const (
	diskEvery   = 30 * time.Minute
	diskRefresh = time.Minute // when the user asks for a fresh look
	diskTimeout = 40 * time.Second
	maxDisk     = 8
)

// disks remembers the last measurement for every workload's snapshot.
type disks struct {
	mu    sync.Mutex
	known map[string]bool
	// pending: a volume was seen that is not measured yet.
	pending bool
	at      time.Time
	disk    *Disk
	volumes map[string]int64
}

var diskUse = &disks{}

func (c *disks) get(ctx context.Context, h caller, volumes []helper.WorkloadVolume, maxAge time.Duration, now time.Time) (*Disk, map[string]int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.known == nil {
		c.known = map[string]bool{}
	}
	fresh := !c.at.IsZero() && now.Sub(c.at) < maxAge
	for _, v := range volumes {
		if !c.known[v.Name] && len(c.known) < 256 {
			c.known[v.Name], c.pending = true, true
		}
	}
	// A new volume is measured soon, but not at once for every new one.
	if c.pending {
		fresh = fresh && now.Sub(c.at) < diskRefresh
	}
	if fresh {
		return c.disk, c.volumes
	}
	// A failure waits its turn too.
	c.at, c.pending = now, false
	ctx, cancel := context.WithTimeout(ctx, diskTimeout)
	defer cancel()
	var out helper.DockerDisk
	if err := h.Call(ctx, "docker.disk", helper.DockerDiskRequest{Volumes: slices.Sorted(maps.Keys(c.known))}, &out); err != nil || len(out.Items) == 0 {
		return c.disk, c.volumes
	}
	c.disk = &Disk{TakenAt: bus.Timestamp(now), Items: out.Items[:min(len(out.Items), maxDisk)]}
	c.volumes = out.Volumes
	return c.disk, c.volumes
}

// measure adds what the workload uses right now. Only before sending: these
// numbers always move, so they are not what makes a snapshot a new one.
func (s *Snapshot) measure(ctx context.Context, h caller, in deploy.Installed, c *disks, maxAge time.Duration, now time.Time) {
	if in.Kind == "systemd" {
		return
	}
	if s.Managed {
		var stats []helper.WorkloadStats
		if err := h.Call(ctx, "workload.stats", helper.WorkloadRequest{Kind: in.Kind, Key: in.Key, Name: in.Name}, &stats); err == nil {
			for i := range s.Containers {
				for j := range stats {
					if stats[j].Name == s.Containers[i].Name {
						s.Containers[i].Stats = &stats[j]
					}
				}
			}
		}
	}
	disk, volumes := c.get(ctx, h, s.Volumes, maxAge, now)
	s.Disk = disk
	for i := range s.Volumes {
		if n, ok := volumes[s.Volumes[i].Name]; ok {
			s.Volumes[i].Size = &n
		}
	}
	s.fit()
}
