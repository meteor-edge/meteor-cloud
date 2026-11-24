package api

import (
	"context"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/storage"
)

// DefaultDeviceMetrics are the readings every meteorcli agent sends.
var DefaultDeviceMetrics = []string{"cpu_percent", "memory_percent", "disk_percent", "temperature_c"}

const maxDeviceMetricNames = 16

var metricName = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,64}$`)

// handleInternalDeviceMetrics serves bucketed device telemetry to the control
// plane, which has already checked that the caller may read this device.
//
// GET /internal/v1/organizations/{org}/devices/{device}/metrics
//
//	?start=RFC3339&end=RFC3339  window (default: the last hour)
//	&bucket=1m                  bucket width (default 1m)
//	&names=cpu_percent,...      metrics (default DefaultDeviceMetrics)
func (s *Server) handleInternalDeviceMetrics(w http.ResponseWriter, r *http.Request) {
	org, dev := r.PathValue("org"), r.PathValue("device")
	orgID, err := uuid.Parse(org)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "org: must be a UUID")
		return
	}
	devID, err := uuid.Parse(dev)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "device: must be a UUID")
		return
	}
	qs := r.URL.Query()
	bad := func(param, msg string) {
		writeError(w, http.StatusBadRequest, "invalid_parameter", param+": "+msg)
	}
	q := storage.DeviceMetricsQuery{OrganizationID: orgID.String(), DeviceID: devID.String(), Names: DefaultDeviceMetrics}
	if v := qs.Get("names"); v != "" {
		names := strings.Split(v, ",")
		if len(names) > maxDeviceMetricNames {
			bad("names", fmt.Sprintf("at most %d metrics", maxDeviceMetricNames))
			return
		}
		seen := map[string]bool{}
		q.Names = nil
		for _, n := range names {
			if !metricName.MatchString(n) {
				bad("names", "metric names are 1-64 characters of A-Z a-z 0-9 . _ : -")
				return
			}
			if !seen[n] {
				seen[n] = true
				q.Names = append(q.Names, n)
			}
		}
	}
	q.End = time.Now().UTC()
	if v := qs.Get("end"); v != "" {
		if q.End, err = parseTime(v); err != nil {
			bad("end", err.Error())
			return
		}
	}
	q.Start = q.End.Add(-time.Hour)
	if v := qs.Get("start"); v != "" {
		if q.Start, err = parseTime(v); err != nil {
			bad("start", err.Error())
			return
		}
	}
	if !q.End.After(q.Start) {
		bad("end", "must be after start")
		return
	}
	bucket := qs.Get("bucket")
	if bucket == "" {
		bucket = "1m"
	}
	if q.Bucket, err = parseBucket(bucket); err != nil {
		bad("bucket", err.Error())
		return
	}
	if buckets := int(q.End.Sub(q.Start)/q.Bucket) + 1; buckets > s.opts.QueryMaxBuckets {
		writeError(w, http.StatusBadRequest, "too_many_buckets",
			fmt.Sprintf("This window and bucket size give %d buckets; the limit is %d.", buckets, s.opts.QueryMaxBuckets))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.opts.QueryTimeout)
	defer cancel()
	series, err := storage.QueryDeviceMetrics(ctx, s.store, q)
	if err != nil {
		s.queryFailed(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"organization_id": q.OrganizationID,
		"device_id":       q.DeviceID,
		"start":           q.Start.Format(time.RFC3339Nano),
		"end":             q.End.Format(time.RFC3339Nano),
		"bucket_seconds":  int(q.Bucket.Seconds()),
		"series":          series,
	})
}
