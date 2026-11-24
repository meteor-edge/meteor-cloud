package helper

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"regexp"
	"strconv"
	"time"

	"meteorcloud/meteorcli/internal/localrpc"
)

// Limits copied from the Python meteorcli-logs helper ("warnings").
const (
	maxWarnings       = 200
	maxWarningMessage = 1000
)

// cursorRe is a journal cursor as journalctl prints it ("s=...;i=..."); it
// cannot start with "-".
var cursorRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9=;_-]{0,511}$`)

// WarningsRequest is the params of logs.warnings. Without AfterCursor the last
// 10 minutes are read.
type WarningsRequest struct {
	AfterCursor string `json:"after_cursor"`
}

// Warning is one journal entry at warning or worse, with only what the cloud
// keeps (the Python helper's fields).
type Warning struct {
	Cursor    string  `json:"cursor"`
	Timestamp *string `json:"timestamp"`
	Priority  *int    `json:"priority"`
	Unit      *string `json:"unit"`
	Source    *string `json:"source"`
	Message   string  `json:"message"`
}

// WarningsReply is the answer of logs.warnings.
type WarningsReply struct {
	Entries []Warning `json:"entries"`
}

func (h Host) logsWarnings(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var p WarningsRequest
	if err := decode(raw, &p); err != nil {
		return nil, err
	}
	// --all: without it journalctl prints fields over 4096 bytes as null and the
	// entry would be lost instead of cut.
	args := []string{"--no-pager", "--quiet", "--all", "--output=json", "--priority=warning", "--lines=" + strconv.Itoa(maxWarnings)}
	switch {
	case p.AfterCursor == "":
		args = append(args, "--since=-10min")
	case cursorRe.MatchString(p.AfterCursor):
		args = append(args, "--after-cursor="+p.AfterCursor)
	default:
		return nil, badRequest("after_cursor is not a journal cursor")
	}
	out, err := h.Run(ctx, "journalctl", args...)
	if err != nil && len(out) == 0 {
		return nil, err
	}
	return WarningsReply{Entries: parseWarnings(out)}, nil
}

// parseWarnings turns journalctl --output=json lines into Warnings.
func parseWarnings(out []byte) []Warning {
	entries := []Warning{}
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 64<<10), 1<<20)
	for sc.Scan() {
		var e map[string]json.RawMessage
		if json.Unmarshal(sc.Bytes(), &e) != nil {
			continue
		}
		msg := journalMessage(e["MESSAGE"])
		if msg == "" {
			continue
		}
		if r := []rune(msg); len(r) > maxWarningMessage {
			msg = string(r[:maxWarningMessage])
		}
		w := Warning{Message: msg, Unit: journalString(e["_SYSTEMD_UNIT"]), Source: journalString(e["SYSLOG_IDENTIFIER"])}
		if c := journalString(e["__CURSOR"]); c != nil {
			w.Cursor = *c
		}
		if s := journalString(e["__REALTIME_TIMESTAMP"]); s != nil {
			if us, err := strconv.ParseInt(*s, 10, 64); err == nil {
				ts := time.UnixMicro(us).UTC().Format("2006-01-02T15:04:05.000000Z")
				w.Timestamp = &ts
			}
		}
		if s := journalString(e["PRIORITY"]); s != nil {
			if n, err := strconv.Atoi(*s); err == nil {
				w.Priority = &n
			}
		}
		entries = append(entries, w)
	}
	if len(entries) > maxWarnings {
		entries = entries[len(entries)-maxWarnings:]
	}
	return entries
}

// journalString returns a string field, nil when missing or not a string.
func journalString(raw json.RawMessage) *string {
	var s string
	if len(raw) == 0 || json.Unmarshal(raw, &s) != nil {
		return nil
	}
	return &s
}

// journalMessage decodes MESSAGE, which journalctl prints as a byte array when
// it is not valid UTF-8.
func journalMessage(raw json.RawMessage) string {
	if s := journalString(raw); s != nil {
		return *s
	}
	var b []int
	if json.Unmarshal(raw, &b) != nil {
		return ""
	}
	buf := make([]byte, 0, len(b))
	for _, v := range b {
		if v >= 0 && v < 256 {
			buf = append(buf, byte(v))
		}
	}
	return string(bytes.ToValidUTF8(buf, []byte("�")))
}
