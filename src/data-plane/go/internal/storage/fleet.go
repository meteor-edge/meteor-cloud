package storage

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// Platform streams written by the control plane (not by devices or applications).
const (
	// DeploymentsStream holds every status change of a device in a Deploy.
	DeploymentsStream = "meteor.deployments"
	// DeviceStatusStream holds the agent's online message and the broker's
	// last will (offline), as written by device-ingest.
	DeviceStatusStream = "meteor.device.status"
	// DeviceEventsStream holds device events such as app lifecycle changes.
	DeviceEventsStream = "meteor.device.events"
)

// DeviceCount is the number of distinct devices that reported in one bucket.
type DeviceCount struct {
	Start   time.Time `json:"t"`
	Devices int64     `json:"devices"`
}

// DeviceCounter is implemented by stores that count distinct devices natively.
type DeviceCounter interface {
	CountDevices(ctx context.Context, q Query, bucket time.Duration) ([]DeviceCount, error)
}

// fallbackScanLimit bounds how many events the generic fallback reads.
var fallbackScanLimit, fallbackPageSize = 200_000, 1000

// ErrScanLimit means the window holds more events than the generic fallback
// reads; counts over part of the window would be wrong, so none are returned.
var ErrScanLimit = errors.New("too many events in the window for the generic device count")

// CountReportingDevices counts, per bucket, the distinct devices with at least one
// event in q (one organization, one stream, a time window). Over the metrics
// stream, which every connected agent writes once a minute, this is the number of
// devices online in that bucket.
func CountReportingDevices(ctx context.Context, store EventStore, q Query, bucket time.Duration) ([]DeviceCount, error) {
	if bucket < time.Second {
		return nil, fmt.Errorf("bucket is too small")
	}
	if c, ok := store.(DeviceCounter); ok {
		return c.CountDevices(ctx, q, bucket)
	}
	// Generic path for PostgreSQL and memory: page through the window.
	seen := map[int64]map[string]struct{}{}
	var order []int64
	q.Limit, q.Descending, q.After = fallbackPageSize, false, nil
	read := 0
	for {
		events, next, err := store.QueryEvents(ctx, q)
		if err != nil {
			return nil, err
		}
		for _, ev := range events {
			if ev.DeviceID == "" {
				continue
			}
			key := ev.Time.Truncate(bucket).UnixMicro()
			set := seen[key]
			if set == nil {
				set = map[string]struct{}{}
				seen[key] = set
				order = append(order, key)
			}
			set[ev.DeviceID] = struct{}{}
		}
		read += len(events)
		if next == nil {
			break
		}
		if read >= fallbackScanLimit {
			return nil, ErrScanLimit
		}
		q.After = next
	}
	out := make([]DeviceCount, 0, len(order))
	for _, key := range order {
		out = append(out, DeviceCount{Start: time.UnixMicro(key).UTC(), Devices: int64(len(seen[key]))})
	}
	return out, nil
}

// CountDevices lets a Split store use its event store's native count.
func (s *Split) CountDevices(ctx context.Context, q Query, bucket time.Duration) ([]DeviceCount, error) {
	return CountReportingDevices(ctx, s.Events, q, bucket)
}
