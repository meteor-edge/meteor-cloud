package storage

import (
	"context"
	"fmt"
	"time"
)

// DeviceMetricsStream is the platform stream that device-ingest writes the agent's
// devices/<id>/metrics messages to (deviceingest.StreamMetrics).
const DeviceMetricsStream = "meteor.device.metrics"

// DeviceMetricsQuery selects bucketed series of numeric device metrics, such as
// cpu_percent, memory_percent, disk_percent or temperature_c, for one device.
type DeviceMetricsQuery struct {
	OrganizationID string
	DeviceID       string
	Names          []string // top-level numeric keys of the metrics payload
	Start, End     time.Time
	Bucket         time.Duration
}

// MetricPoint is one bucket of one metric. Buckets without samples are left out.
type MetricPoint struct {
	Start time.Time `json:"t"`
	Avg   float64   `json:"avg"`
	Min   float64   `json:"min"`
	Max   float64   `json:"max"`
	Count int64     `json:"count"`
}

// MetricSeries is the points of one metric in time order.
type MetricSeries struct {
	Name   string        `json:"name"`
	Points []MetricPoint `json:"points"`
}

// DeviceMetricsReader is implemented by stores with a dedicated, typed metrics
// table (ClickHouse). Other stores are served by QueryDeviceMetrics through the
// generic Aggregate.
type DeviceMetricsReader interface {
	DeviceMetrics(ctx context.Context, q DeviceMetricsQuery) ([]MetricSeries, error)
}

// QueryDeviceMetrics returns one series per requested name, in the order asked,
// with an empty series for a name that has no samples.
func QueryDeviceMetrics(ctx context.Context, store EventStore, q DeviceMetricsQuery) ([]MetricSeries, error) {
	if q.Bucket < time.Second {
		return nil, fmt.Errorf("bucket is too small")
	}
	if r, ok := store.(DeviceMetricsReader); ok {
		got, err := r.DeviceMetrics(ctx, q)
		if err != nil {
			return nil, err
		}
		return orderSeries(q.Names, got), nil
	}
	base := Query{OrganizationID: q.OrganizationID, Stream: DeviceMetricsStream, DeviceID: q.DeviceID, Start: q.Start, End: q.End}
	out := make([]MetricSeries, 0, len(q.Names))
	for _, name := range q.Names {
		byStart := map[int64]*MetricPoint{}
		var order []int64
		for _, fn := range []string{"avg", "min", "max"} {
			buckets, err := store.Aggregate(ctx, AggregateQuery{Query: base, Field: []string{name}, Func: fn, Bucket: q.Bucket})
			if err != nil {
				return nil, err
			}
			for _, b := range buckets {
				if b.Value == nil || b.Count == 0 {
					continue
				}
				key := b.Start.UnixMicro()
				p := byStart[key]
				if p == nil {
					p = &MetricPoint{Start: b.Start, Count: b.Count}
					byStart[key] = p
					order = append(order, key)
				}
				switch fn {
				case "avg":
					p.Avg = *b.Value
				case "min":
					p.Min = *b.Value
				case "max":
					p.Max = *b.Value
				}
			}
		}
		series := MetricSeries{Name: name, Points: make([]MetricPoint, 0, len(order))}
		for _, key := range order {
			series.Points = append(series.Points, *byStart[key])
		}
		out = append(out, series)
	}
	return out, nil
}

func orderSeries(names []string, got []MetricSeries) []MetricSeries {
	byName := make(map[string]MetricSeries, len(got))
	for _, s := range got {
		byName[s.Name] = s
	}
	out := make([]MetricSeries, 0, len(names))
	for _, name := range names {
		s, ok := byName[name]
		if !ok || s.Points == nil {
			s = MetricSeries{Name: name, Points: []MetricPoint{}}
		}
		out = append(out, s)
	}
	return out
}

// DeviceMetrics lets a Split store use its event store's typed metrics table.
func (s *Split) DeviceMetrics(ctx context.Context, q DeviceMetricsQuery) ([]MetricSeries, error) {
	return QueryDeviceMetrics(ctx, s.Events, q)
}
