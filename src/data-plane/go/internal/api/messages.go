package api

import (
	"errors"
	"net/http"
	"time"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/ingest"
)

type messagesRequest struct {
	Messages []envelope.Message `json:"messages"`
}

type messageResult struct {
	Index  int                 `json:"index"`
	ID     string              `json:"id,omitempty"`
	Status string              `json:"status"` // stored | duplicate | rejected
	Error  *envelope.Rejection `json:"error,omitempty"`
}

// handleMessages is the HTTP ingestion endpoint. A 200 means every message with
// status "stored" or "duplicate" is durably persisted. 503 means nothing from this
// request should be assumed stored: retry with the same ids.
func (s *Server) handleMessages(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	var req messagesRequest
	limit := int64(s.opts.MaxBatch)*int64(s.opts.Limits.MaxPayloadBytes+1024) + 4096
	if limit > 32<<20 {
		limit = 32 << 20
	}
	if !decodeBody(w, r, limit, &req) {
		return
	}
	if len(req.Messages) == 0 {
		writeError(w, http.StatusBadRequest, "no_messages", "Send at least one message.")
		return
	}
	if len(req.Messages) > s.opts.MaxBatch {
		writeError(w, http.StatusRequestEntityTooLarge, "batch_too_large", "Too many messages in one request.")
		return
	}
	// The request itself spent one token; a batch spends one per extra message.
	if extra := len(req.Messages) - 1; extra > 0 && !s.allow(id.CredentialID, extra) {
		s.rateLimited(w)
		return
	}
	s.metrics.MessagesReceived.WithLabelValues("http").Add(float64(len(req.Messages)))

	now := time.Now()
	results := make([]messageResult, len(req.Messages))
	accepted := make([]*envelope.Event, 0, len(req.Messages))
	positions := make([]int, 0, len(req.Messages))
	for i := range req.Messages {
		ev, rej := envelope.Normalize(&req.Messages[i], id, now, s.opts.Limits)
		if rej != nil {
			s.metrics.MessagesRejected.WithLabelValues("http", rej.Code).Inc()
			results[i] = messageResult{Index: i, ID: req.Messages[i].ID, Status: "rejected", Error: rej}
			continue
		}
		accepted = append(accepted, ev)
		positions = append(positions, i)
	}
	if len(accepted) > 0 {
		statuses, err := s.pipeline.Submit(r.Context(), accepted)
		switch {
		case errors.Is(err, ingest.ErrOverloaded), errors.Is(err, ingest.ErrStopped):
			s.metrics.MessagesRejected.WithLabelValues("http", "overloaded").Add(float64(len(accepted)))
			w.Header().Set("Retry-After", "1")
			writeError(w, http.StatusServiceUnavailable, "overloaded", "The data plane is busy. Retry with the same message ids.")
			return
		case err != nil:
			w.Header().Set("Retry-After", "2")
			writeError(w, http.StatusServiceUnavailable, "storage_unavailable", "The messages could not be stored. Retry with the same message ids.")
			return
		}
		for j, pos := range positions {
			results[pos] = messageResult{Index: pos, ID: accepted[j].ID, Status: statuses[j]}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"results":  results,
		"accepted": len(accepted),
		"rejected": len(req.Messages) - len(accepted),
	})
}
