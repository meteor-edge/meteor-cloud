package api

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/storage"
	"meteorcloud/dataplane/internal/streams"
)

func encodeCursor(c *storage.Cursor) string {
	raw := strconv.FormatInt(c.Time.UnixNano(), 10) + "|" + c.ID
	return base64.RawURLEncoding.EncodeToString([]byte(raw))
}

func decodeCursor(s string) (*storage.Cursor, error) {
	raw, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return nil, err
	}
	ts, id, ok := strings.Cut(string(raw), "|")
	if !ok {
		return nil, errors.New("malformed cursor")
	}
	nanos, err := strconv.ParseInt(ts, 10, 64)
	if err != nil {
		return nil, err
	}
	return &storage.Cursor{Time: time.Unix(0, nanos).UTC(), ID: id}, nil
}

// parseBucket accepts "30s", "5m", "1h", "1d".
func parseBucket(s string) (time.Duration, error) {
	if strings.HasSuffix(s, "d") {
		n, err := strconv.Atoi(strings.TrimSuffix(s, "d"))
		if err != nil || n < 1 {
			return 0, errors.New("invalid bucket")
		}
		return time.Duration(n) * 24 * time.Hour, nil
	}
	d, err := time.ParseDuration(s)
	if err != nil || d < time.Second {
		return 0, errors.New("bucket must be at least 1s, for example 30s, 5m, 1h or 1d")
	}
	return d, nil
}

func parseTime(v string) (time.Time, error) {
	t, err := time.Parse(time.RFC3339Nano, v)
	if err != nil {
		return time.Time{}, errors.New("must be an RFC 3339 timestamp such as 2026-11-11T00:00:00Z")
	}
	return t.UTC(), nil
}

func validField(path []string) bool {
	if len(path) == 0 || len(path) > 4 {
		return false
	}
	for _, p := range path {
		if p == "" || len(p) > 64 {
			return false
		}
	}
	return true
}

// handleStreamData serves historical data: raw events with keyset pagination, or a
// bucketed aggregate of one numeric field. Every query is bounded in rows, buckets
// and time.
func (s *Server) handleStreamData(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	stream := r.PathValue("stream")
	if err := streams.ValidateName(stream); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_stream", err.Error())
		return
	}
	if !id.CanRead(stream) {
		writeError(w, http.StatusForbidden, "forbidden_stream", fmt.Sprintf("This application may not read stream %q.", stream))
		return
	}
	qs := r.URL.Query()
	q := storage.Query{OrganizationID: id.OrganizationID, Stream: stream, DeviceID: qs.Get("device_id"),
		Application: qs.Get("application"), Kind: qs.Get("kind")}
	bad := func(param, msg string) {
		writeError(w, http.StatusBadRequest, "invalid_parameter", param+": "+msg)
	}
	var err error
	if v := qs.Get("start"); v != "" {
		if q.Start, err = parseTime(v); err != nil {
			bad("start", err.Error())
			return
		}
	}
	if v := qs.Get("end"); v != "" {
		if q.End, err = parseTime(v); err != nil {
			bad("end", err.Error())
			return
		}
	}
	if !q.Start.IsZero() && !q.End.IsZero() && !q.End.After(q.Start) {
		bad("end", "must be after start")
		return
	}
	if v := qs.Get("filter"); v != "" {
		if len(v) > 2048 || json.Unmarshal([]byte(v), &q.Filter) != nil || len(q.Filter) > 8 {
			bad("filter", `must be a small JSON object such as {"severity":"warning"}`)
			return
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.opts.QueryTimeout)
	defer cancel()

	if fn := qs.Get("aggregate"); fn != "" {
		s.aggregate(ctx, w, q, fn, qs.Get("field"), qs.Get("bucket"))
		return
	}

	q.Limit = 100
	if v := qs.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > s.opts.QueryMaxLimit {
			bad("limit", fmt.Sprintf("must be between 1 and %d", s.opts.QueryMaxLimit))
			return
		}
		q.Limit = n
	}
	switch qs.Get("order") {
	case "", "asc":
	case "desc":
		q.Descending = true
	default:
		bad("order", "must be asc or desc")
		return
	}
	if v := qs.Get("cursor"); v != "" {
		if q.After, err = decodeCursor(v); err != nil {
			bad("cursor", "is not a cursor returned by this API")
			return
		}
	}
	events, next, err := s.store.QueryEvents(ctx, q)
	if err != nil {
		s.queryFailed(w, err)
		return
	}
	if events == nil {
		events = []*envelope.Event{}
	}
	resp := map[string]any{"stream": stream, "events": events, "next_cursor": nil}
	if next != nil {
		resp["next_cursor"] = encodeCursor(next)
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) aggregate(ctx context.Context, w http.ResponseWriter, q storage.Query, fn, field, bucket string) {
	switch fn {
	case "avg", "min", "max", "sum", "count":
	default:
		writeError(w, http.StatusBadRequest, "invalid_parameter", "aggregate: must be avg, min, max, sum or count")
		return
	}
	path := strings.Split(field, ".")
	if fn == "count" && field == "" {
		path = []string{"_"}
	} else if !validField(path) {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "field: name the numeric field to aggregate, for example power or phases.l1")
		return
	}
	if bucket == "" {
		bucket = "1m"
	}
	width, err := parseBucket(bucket)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "bucket: "+err.Error())
		return
	}
	// An aggregate always has a bounded window: default to the last 24 hours.
	if q.End.IsZero() {
		q.End = time.Now().UTC()
	}
	if q.Start.IsZero() {
		q.Start = q.End.Add(-24 * time.Hour)
	}
	if buckets := int(q.End.Sub(q.Start)/width) + 1; buckets > s.opts.QueryMaxBuckets {
		writeError(w, http.StatusBadRequest, "too_many_buckets",
			fmt.Sprintf("This window and bucket size give %d buckets; the limit is %d. Use a larger bucket or a shorter window.", buckets, s.opts.QueryMaxBuckets))
		return
	}
	result, err := s.store.Aggregate(ctx, storage.AggregateQuery{Query: q, Field: path, Func: fn, Bucket: width})
	if err != nil {
		s.queryFailed(w, err)
		return
	}
	if result == nil {
		result = []storage.Bucket{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"stream":    q.Stream,
		"aggregate": map[string]any{"function": fn, "field": field, "bucket_seconds": int(width.Seconds())},
		"start":     q.Start.Format(time.RFC3339Nano),
		"end":       q.End.Format(time.RFC3339Nano),
		"buckets":   result,
	})
}

func (s *Server) queryFailed(w http.ResponseWriter, err error) {
	s.metrics.StorageErrors.WithLabelValues("query").Inc()
	if errors.Is(err, context.DeadlineExceeded) || strings.Contains(err.Error(), "statement timeout") {
		writeError(w, http.StatusGatewayTimeout, "query_timeout", "The query took too long. Narrow the time range or use a larger bucket.")
		return
	}
	s.log.Error("query failed", "error", err.Error())
	writeError(w, http.StatusServiceUnavailable, "storage_unavailable", "Historical data is temporarily unavailable.")
}
