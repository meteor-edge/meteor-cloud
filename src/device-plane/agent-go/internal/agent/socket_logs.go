package agent

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"time"

	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/localrpc"
)

// Logs over the agent socket: `meteorcli logs` for members of the meteorcli
// group, who may not talk to the root helper themselves. The agent asks the
// helper and passes the answer on; a followed log streams line by line until
// the user stops it.

// maxLogStreams bounds followed logs at once, so they never take every slot
// the agent has on the helper socket.
const maxLogStreams = 8

func logsHandlers(d Deps) map[string]localrpc.Handler {
	streams := make(chan struct{}, maxLogStreams)
	return map[string]localrpc.Handler{
		"logs.list": {Run: func(ctx context.Context, _ localrpc.Peer, _ json.RawMessage) (any, error) {
			var reply helper.LogsListReply
			if err := d.Helper.Call(ctx, "logs.list", nil, &reply); err != nil {
				return nil, passOn(err)
			}
			return reply, nil
		}},
		"logs.show": {
			Timeout: 24 * time.Hour,
			Audit: func(raw json.RawMessage) []any {
				var r helper.ShowLogsRequest
				_ = json.Unmarshal(raw, &r)
				return []any{"kind", r.Kind, "name", r.Name, "follow", r.Follow}
			},
			Stream: func(ctx context.Context, _ localrpc.Peer, raw json.RawMessage, w io.Writer) error {
				var r helper.ShowLogsRequest
				if err := json.Unmarshal(raw, &r); err != nil {
					return localrpc.Errorf(localrpc.CodeBadRequest, "params: %v", err)
				}
				if err := r.Validate(); err != nil {
					return err
				}
				select {
				case streams <- struct{}{}:
					defer func() { <-streams }()
				default:
					return localrpc.Errorf(localrpc.CodeBusy, "too many logs are being read at once; try again later")
				}
				err := d.Helper.Stream(ctx, "logs.show", r, func(line string) error {
					_, err := io.WriteString(w, line+"\n")
					return err
				})
				return passOn(err)
			},
		},
	}
}

// passOn returns the helper's own error (its code and message) to the CLI.
func passOn(err error) error {
	var e *localrpc.Error
	if errors.As(err, &e) {
		return e
	}
	return err
}
