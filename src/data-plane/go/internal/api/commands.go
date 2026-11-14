package api

import (
	"encoding/json"
	"errors"
	"net/http"

	"meteorcloud/dataplane/internal/commands"
	"meteorcloud/dataplane/internal/storage"
)

type commandCreateBody struct {
	Application      string          `json:"application"`
	DeviceID         string          `json:"device_id"`
	Name             string          `json:"name"`
	Payload          json.RawMessage `json:"payload"`
	TimeoutSeconds   int             `json:"timeout_seconds"`
	ExpiresInSeconds int             `json:"expires_in_seconds"`
	MaxAttempts      int             `json:"max_attempts"`
	IdempotencyKey   string          `json:"idempotency_key"`
}

func (s *Server) commandError(w http.ResponseWriter, err error) {
	var invalid *commands.InvalidError
	switch {
	case errors.As(err, &invalid):
		writeError(w, http.StatusBadRequest, "invalid_command", invalid.Msg)
	case errors.Is(err, commands.ErrForbidden):
		writeError(w, http.StatusForbidden, "forbidden_command", "This application may not act on this command.")
	case errors.Is(err, storage.ErrNotFound):
		writeError(w, http.StatusNotFound, "command_not_found", "Command was not found.")
	default:
		s.log.Error("command operation failed", "error", err.Error())
		writeError(w, http.StatusServiceUnavailable, "storage_unavailable", "The command could not be processed. Retry.")
	}
}

// handleCommandCreate lets an application command another application it has an
// explicit send_commands grant for. Operators go through the control plane instead.
func (s *Server) handleCommandCreate(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	var body commandCreateBody
	if !decodeBody(w, r, int64(s.opts.Limits.MaxPayloadBytes)+4096, &body) {
		return
	}
	if !id.CanSendCommandTo(body.Application) {
		writeError(w, http.StatusForbidden, "forbidden_command",
			"This application has no send_commands permission for that application.")
		return
	}
	cmd, err := s.commands.Create(r.Context(), commands.CreateRequest{
		OrganizationID: id.OrganizationID, Application: body.Application, DeviceID: body.DeviceID, Name: body.Name,
		Payload: body.Payload, TimeoutSeconds: body.TimeoutSeconds, ExpiresInSeconds: body.ExpiresInSeconds,
		MaxAttempts: body.MaxAttempts, IdempotencyKey: body.IdempotencyKey, IssuedBy: "app:" + id.ApplicationID,
	})
	if err != nil {
		s.commandError(w, err)
		return
	}
	writeJSON(w, http.StatusAccepted, cmd)
}

func (s *Server) handleCommandGet(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	cmd, err := s.commands.Get(r.Context(), id.OrganizationID, r.PathValue("id"))
	if err != nil {
		s.commandError(w, err)
		return
	}
	if !commands.CanSee(id, cmd) {
		// Do not reveal that the command exists.
		writeError(w, http.StatusNotFound, "command_not_found", "Command was not found.")
		return
	}
	writeJSON(w, http.StatusOK, cmd)
}

// handleCommandsPending is the polling fallback for targets without a streaming
// connection: commands not yet acknowledged as received.
func (s *Server) handleCommandsPending(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	if !id.Permissions.ReceiveCommands {
		writeError(w, http.StatusForbidden, "forbidden_command", "This application has no receive_commands permission.")
		return
	}
	pending, err := s.commands.Undelivered(r.Context(), id)
	if err != nil {
		s.commandError(w, err)
		return
	}
	if pending == nil {
		pending = []*storage.Command{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"commands": pending})
}

type commandAckBody struct {
	Status string          `json:"status"`
	Result json.RawMessage `json:"result"`
	Error  string          `json:"error"`
}

func (s *Server) handleCommandAck(w http.ResponseWriter, r *http.Request) {
	id := identityFrom(r)
	var body commandAckBody
	if !decodeBody(w, r, int64(s.opts.Limits.MaxPayloadBytes)+4096, &body) {
		return
	}
	cmd, err := s.commands.Ack(r.Context(), id, r.PathValue("id"), body.Status, body.Result, body.Error)
	if err != nil {
		s.commandError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, cmd)
}

// ---- internal API (control plane) ------------------------------------------

type internalCommandBody struct {
	OrganizationID string `json:"organization_id"`
	ApplicationID  string `json:"application_id"`
	IssuedBy       string `json:"issued_by"`
	commandCreateBody
}

// handleInternalCommandCreate accepts a command the control plane already authorized.
func (s *Server) handleInternalCommandCreate(w http.ResponseWriter, r *http.Request) {
	var body internalCommandBody
	if !decodeBody(w, r, int64(s.opts.Limits.MaxPayloadBytes)+4096, &body) {
		return
	}
	if body.OrganizationID == "" || body.ApplicationID == "" {
		writeError(w, http.StatusBadRequest, "invalid_command", "organization_id and application_id are required.")
		return
	}
	issuedBy := body.IssuedBy
	if issuedBy == "" {
		issuedBy = "control-plane"
	}
	cmd, err := s.commands.Create(r.Context(), commands.CreateRequest{
		OrganizationID: body.OrganizationID, ApplicationID: body.ApplicationID, DeviceID: body.DeviceID,
		Name: body.Name, Payload: body.Payload, TimeoutSeconds: body.TimeoutSeconds,
		ExpiresInSeconds: body.ExpiresInSeconds, MaxAttempts: body.MaxAttempts,
		IdempotencyKey: body.IdempotencyKey, IssuedBy: issuedBy,
	})
	if err != nil {
		s.commandError(w, err)
		return
	}
	writeJSON(w, http.StatusAccepted, cmd)
}

func (s *Server) handleInternalCommandGet(w http.ResponseWriter, r *http.Request) {
	cmd, err := s.commands.Get(r.Context(), r.PathValue("org"), r.PathValue("id"))
	if err != nil {
		s.commandError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, cmd)
}
