package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/storage"
)

// Platform history for the control plane: deployment changes it records, and the
// device streams device-ingest writes (status, events, metrics). Applications
// never reach these handlers; they live on the ops listener behind the internal
// token, and the control plane checks the user's permissions first.

// writableStreams are the platform streams the control plane may write.
var writableStreams = map[string]bool{storage.DeploymentsStream: true}

const maxPlatformEvents = 500

type platformEvent struct {
	ID             string          `json:"id"`
	OrganizationID string          `json:"organization_id"`
	DeviceID       string          `json:"device_id"`
	Stream         string          `json:"stream"`
	Type           string          `json:"type"`
	Time           time.Time       `json:"time"`
	Data           json.RawMessage `json:"data"`
}

// handleInternalEventsWrite stores platform events (POST /internal/v1/events).
// Ids are the control plane's outbox ids, so a retried batch is stored once.
func (s *Server) handleInternalEventsWrite(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Events []platformEvent `json:"events"`
	}
	if !decodeBody(w, r, int64(maxPlatformEvents)*int64(s.opts.Limits.MaxPayloadBytes/8+1024), &body) {
		return
	}
	if len(body.Events) == 0 || len(body.Events) > maxPlatformEvents {
		writeError(w, http.StatusBadRequest, "invalid_events", fmt.Sprintf("Send 1 to %d events.", maxPlatformEvents))
		return
	}
	now := time.Now().UTC()
	events := make([]*envelope.Event, 0, len(body.Events))
	for i, e := range body.Events {
		bad := func(msg string) {
			writeError(w, http.StatusBadRequest, "invalid_events", fmt.Sprintf("events[%d]: %s", i, msg))
		}
		if !writableStreams[e.Stream] {
			bad("stream must be one of the platform streams the control plane writes")
			return
		}
		if !envelope.ValidID(e.ID) {
			bad("id is not a valid event id")
			return
		}
		org, err := uuid.Parse(e.OrganizationID)
		if err != nil {
			bad("organization_id must be a UUID")
			return
		}
		dev := ""
		if e.DeviceID != "" {
			id, err := uuid.Parse(e.DeviceID)
			if err != nil {
				bad("device_id must be a UUID")
				return
			}
			dev = id.String()
		}
		if e.Type == "" || len(e.Type) > 128 {
			bad("type is required (at most 128 characters)")
			return
		}
		if e.Time.IsZero() || e.Time.After(now.Add(s.opts.Limits.MaxFutureSkew+time.Minute)) {
			bad("time is missing or in the future")
			return
		}
		if len(e.Data) == 0 {
			e.Data = json.RawMessage(`{}`)
		}
		data, rej := envelope.CanonicalData(e.Data, s.opts.Limits.MaxPayloadBytes)
		if rej != nil {
			bad(rej.Message)
			return
		}
		events = append(events, &envelope.Event{
			ID: e.ID, SchemaVersion: envelope.SchemaVersion, Kind: envelope.KindEvent, Type: e.Type, Stream: e.Stream,
			OrganizationID: org.String(), DeviceID: dev, Time: e.Time.UTC(), ReceivedAt: now, Data: data,
		})
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.opts.QueryTimeout)
	defer cancel()
	inserted, err := s.store.InsertEvents(ctx, events)
	if err != nil {
		s.metrics.StorageErrors.WithLabelValues("insert").Inc()
		s.log.Error("platform events insert failed", "error", err.Error())
		writeError(w, http.StatusServiceUnavailable, "storage_unavailable", "Events could not be stored; retry later.")
		return
	}
	n := 0
	for _, ok := range inserted {
		if ok {
			n++
		}
	}
	writeJSON(w, http.StatusOK, map[string]int{"received": len(events), "inserted": n})
}

// handleInternalEventsQuery reads one platform stream of an organization
// (GET /internal/v1/organizations/{org}/events).
//
//	?stream=meteor.device.status   required, a meteor.* stream
//	&device_id=<uuid>              optional
//	&start, &end                   RFC 3339 window
//	&order=asc|desc&limit=&cursor= paging, as on /v1/streams/{stream}/data
//	&filter={"deployment_id":"…"}  top-level equality on the data
//	&q=text                        "message" contains text, ignoring case (logs)
//	&previous=1                    also return the last event before start
//	                               (the state a timeline starts in)
func (s *Server) handleInternalEventsQuery(w http.ResponseWriter, r *http.Request) {
	org, err := uuid.Parse(r.PathValue("org"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "org: must be a UUID")
		return
	}
	qs := r.URL.Query()
	bad := func(param, msg string) {
		writeError(w, http.StatusBadRequest, "invalid_parameter", param+": "+msg)
	}
	stream := qs.Get("stream")
	if !strings.HasPrefix(stream, "meteor.") || len(stream) > 128 {
		bad("stream", "must be a platform stream (meteor.*)")
		return
	}
	q := storage.Query{OrganizationID: org.String(), Stream: stream, Limit: 100}
	if v := qs.Get("device_id"); v != "" {
		id, err := uuid.Parse(v)
		if err != nil {
			bad("device_id", "must be a UUID")
			return
		}
		q.DeviceID = id.String()
	}
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
	if v := qs.Get("filter"); v != "" {
		if len(v) > 2048 || json.Unmarshal([]byte(v), &q.Filter) != nil || len(q.Filter) > 8 {
			bad("filter", `must be a small JSON object such as {"deployment_id":"…"}`)
			return
		}
	}
	if v := qs.Get("q"); v != "" {
		if utf8.RuneCountInString(v) > 200 {
			bad("q", "must be at most 200 characters")
			return
		}
		q.Contains = v
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.opts.QueryTimeout)
	defer cancel()
	events, next, err := s.store.QueryEvents(ctx, q)
	if err != nil {
		s.queryFailed(w, err)
		return
	}
	if events == nil {
		events = []*envelope.Event{}
	}
	resp := map[string]any{"stream": stream, "events": events, "next_cursor": nil, "previous": nil}
	if next != nil {
		resp["next_cursor"] = encodeCursor(next)
	}
	if qs.Get("previous") == "1" && !q.Start.IsZero() {
		pq := q
		pq.Start, pq.End, pq.After, pq.Limit, pq.Descending = time.Time{}, q.Start, nil, 1, true
		before, _, err := s.store.QueryEvents(ctx, pq)
		if err != nil {
			s.queryFailed(w, err)
			return
		}
		if len(before) == 1 {
			resp["previous"] = before[0]
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleInternalOnlineDevices counts the devices that reported metrics per bucket
// (GET /internal/v1/organizations/{org}/devices/online?start&end&bucket).
func (s *Server) handleInternalOnlineDevices(w http.ResponseWriter, r *http.Request) {
	org, err := uuid.Parse(r.PathValue("org"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_parameter", "org: must be a UUID")
		return
	}
	qs := r.URL.Query()
	bad := func(param, msg string) {
		writeError(w, http.StatusBadRequest, "invalid_parameter", param+": "+msg)
	}
	q := storage.Query{OrganizationID: org.String(), Stream: storage.DeviceMetricsStream, End: time.Now().UTC()}
	if v := qs.Get("end"); v != "" {
		if q.End, err = parseTime(v); err != nil {
			bad("end", err.Error())
			return
		}
	}
	q.Start = q.End.Add(-24 * time.Hour)
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
		bucket = "5m"
	}
	width, err := parseBucket(bucket)
	if err != nil {
		bad("bucket", err.Error())
		return
	}
	if buckets := int(q.End.Sub(q.Start)/width) + 1; buckets > s.opts.QueryMaxBuckets {
		writeError(w, http.StatusBadRequest, "too_many_buckets",
			fmt.Sprintf("This window and bucket size give %d buckets; the limit is %d.", buckets, s.opts.QueryMaxBuckets))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.opts.QueryTimeout)
	defer cancel()
	counts, err := storage.CountReportingDevices(ctx, s.store, q, width)
	if errors.Is(err, storage.ErrScanLimit) {
		writeError(w, http.StatusBadRequest, "window_too_large",
			"Too many events in this window for this storage backend; narrow the time range (ClickHouse has no limit).")
		return
	}
	if err != nil {
		s.queryFailed(w, err)
		return
	}
	if counts == nil {
		counts = []storage.DeviceCount{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"organization_id": q.OrganizationID,
		"start":           q.Start.Format(time.RFC3339Nano),
		"end":             q.End.Format(time.RFC3339Nano),
		"bucket_seconds":  int(width.Seconds()),
		"buckets":         counts,
	})
}
