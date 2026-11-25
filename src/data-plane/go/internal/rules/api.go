package rules

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
)

// API serves the rule engine's internal routes for the control plane: rule tests
// against sample events and the evaluation status of a rule. Every request needs
// the X-Data-Plane-Internal-Token header. It must stay on the private network.
type API struct {
	Engine *engine.Engine
	Token  string
	Limits dsl.Limits
}

// StatusEntry is the evaluation state of one rule and device, without node internals.
type StatusEntry struct {
	DeviceID      string             `json:"device_id"`
	RuleVersion   int                `json:"rule_version"`
	Phase         string             `json:"phase"`
	PhaseSince    time.Time          `json:"phase_since"`
	Episode       int64              `json:"episode"`
	CooldownUntil *time.Time         `json:"cooldown_until,omitempty"`
	LastEvaluated time.Time          `json:"last_evaluated"`
	LastEventID   string             `json:"last_event_id,omitempty"`
	LastValues    map[string]float64 `json:"last_values,omitempty"`
	TimerDue      *time.Time         `json:"timer_due,omitempty"`
}

func (a *API) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/rules/simulate", a.simulate)
	mux.HandleFunc("GET /internal/rules/status/{rule_id}", a.status)
	mux.HandleFunc("GET /internal/rules/engine", a.engineInfo)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := r.Header.Get("X-Data-Plane-Internal-Token")
		if a.Token == "" || subtle.ConstantTimeCompare([]byte(got), []byte(a.Token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid internal token"})
			return
		}
		mux.ServeHTTP(w, r)
	})
}

func (a *API) simulate(w http.ResponseWriter, r *http.Request) {
	var req engine.SimRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8<<20)).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	res, err := engine.Simulate(r.Context(), req, a.Limits, false)
	if err != nil {
		var ve *dsl.ValidationError
		if errors.As(err, &ve) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"error": "invalid rule", "problems": ve.Problems})
			return
		}
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func (a *API) status(w http.ResponseWriter, r *http.Request) {
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	states, err := a.Engine.Status(r.Context(), r.PathValue("rule_id"), r.URL.Query().Get("after"), limit)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		return
	}
	out := make([]StatusEntry, 0, len(states))
	for _, st := range states {
		e := StatusEntry{DeviceID: st.DeviceID, RuleVersion: st.RuleVersion, Phase: st.Phase, PhaseSince: st.PhaseSince,
			Episode: st.Episode, LastEvaluated: st.LastEvaluated, LastEventID: st.LastEventID, LastValues: st.LastValues}
		if !st.CooldownUntil.IsZero() {
			e.CooldownUntil = &st.CooldownUntil
		}
		if !st.TimerDue.IsZero() {
			e.TimerDue = &st.TimerDue
		}
		out = append(out, e)
	}
	writeJSON(w, http.StatusOK, map[string]any{"devices": out})
}

func (a *API) engineInfo(w http.ResponseWriter, _ *http.Request) {
	rs := a.Engine.Rules()
	if rs == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ready": false})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ready": true, "snapshot_version": rs.Version, "rules": rs.Len(),
		"refused": rs.Errors})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
