package rules

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"meteorcloud/dataplane/internal/rules/dispatch"
	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

// API serves the rule engine's internal routes for the control plane: rule tests
// against sample events and the evaluation status of a rule. Every request needs
// the X-Data-Plane-Internal-Token header. It must stay on the private network.
type API struct {
	Engine *engine.Engine
	Token  string
	Limits dsl.Limits
	// Sender delivers test messages to notification integrations; nil when the
	// dispatcher runs elsewhere.
	Sender *dispatch.WebhookSender
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
	mux.HandleFunc("POST /internal/rules/test-delivery", a.testDelivery)
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
		if st.Retired() {
			continue
		}
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
	channels := map[string]bool{"email": a.Sender != nil && a.Sender.Email != nil}
	if rs == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ready": false, "channels": channels})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ready": true, "snapshot_version": rs.Version, "rules": rs.Len(),
		"refused": rs.Errors, "channels": channels})
}

// testDelivery sends one sample alert to an integration, now and without retries, and
// answers whether it arrived. The request only names the integration: where it goes and
// its secret come from the control plane, which has already checked who may do this.
func (a *API) testDelivery(w http.ResponseWriter, r *http.Request) {
	var req struct {
		OrganizationID string `json:"organization_id"`
		IntegrationID  string `json:"integration_id"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	var resolver dispatch.TestResolver
	if a.Sender != nil {
		resolver, _ = a.Sender.Resolver.(dispatch.TestResolver)
	}
	if resolver == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "this rule processor does not deliver notifications"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 25*time.Second)
	defer cancel()
	d, err := resolver.ResolveTest(ctx, req.OrganizationID, req.IntegrationID)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "the control plane did not answer for this integration"})
		return
	}
	now := time.Now().UTC()
	key := "test:" + d.OrganizationID + ":" + strconv.FormatInt(now.UnixNano(), 36)
	p := &engine.ActionPayload{RuleName: "Test notification", OrganizationID: d.OrganizationID, DeviceID: "example",
		Severity: dsl.SeverityInfo, Transition: engine.Transition{Kind: engine.TransitionFiring, At: now,
			Values: map[string]float64{"cpu_percent": 92.5}}}
	if _, err := a.Sender.Deliver(ctx, d, state.OutboxItem{Key: key}, p); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"delivered": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"delivered": true})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
