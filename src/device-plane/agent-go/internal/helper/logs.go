package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"meteorcloud/meteorcli/internal/localrpc"
)

// Limits copied from the Python meteorcli-logs helper.
const (
	defaultLines = 200
	maxLines     = 10000
	maxGrep      = 200
	// With grep, the last N matches are searched for in this many recent lines.
	grepScanLines = 50000
)

var sinceRe = regexp.MustCompile(`^([1-9][0-9]{0,4})([mhd])$`)

var sinceUnits = map[string]string{"m": "min", "h": "h", "d": "d"}

// LogsRequest is the params of logs.read.
type LogsRequest struct {
	// Source is "journal" (everything) or "systemd" (one unit).
	Source string `json:"source"`
	// Unit is the unit for Source "systemd", e.g. "nginx.service".
	Unit string `json:"unit"`
	// Lines is how many lines to return, 1 to 10000 (default 200).
	Lines int `json:"lines"`
	// Since is 15m, 1h, 7d, ... (optional).
	Since string `json:"since"`
	// Grep keeps lines that contain this text, ignoring ASCII case (optional).
	Grep string `json:"grep"`
}

// LogsReply is the answer of logs.read.
type LogsReply struct {
	Lines []string `json:"lines"`
}

func (r *LogsRequest) validate() error {
	bad := func(format string, args ...any) error {
		return localrpc.Errorf(localrpc.CodeBadRequest, format, args...)
	}
	switch r.Source {
	case "journal":
		if r.Unit != "" {
			return bad("unit is only for source systemd")
		}
	case "systemd":
		if !validName.MatchString(r.Unit) || !strings.HasSuffix(r.Unit, ".service") {
			return bad("unit must be a .service name of a-z, 0-9, '.', '_' or '-'")
		}
	default:
		return bad("source must be journal or systemd (docker and files come later)")
	}
	if r.Lines == 0 {
		r.Lines = defaultLines
	}
	if r.Lines < 1 || r.Lines > maxLines {
		return bad("lines must be 1..%d", maxLines)
	}
	if r.Since != "" && !sinceRe.MatchString(r.Since) {
		return bad("since must look like 15m, 1h or 7d")
	}
	if len(r.Grep) > maxGrep || strings.IndexFunc(r.Grep, unicode.IsControl) >= 0 {
		return bad("grep must be at most %d printable characters", maxGrep)
	}
	return nil
}

// journalArgs is the fixed argument list for journalctl.
func (r *LogsRequest) journalArgs() []string {
	lines := r.Lines
	if r.Grep != "" {
		lines = grepScanLines
	}
	args := []string{"--no-pager", "--quiet", "--output=short-iso", "--lines=" + strconv.Itoa(lines)}
	if r.Source == "systemd" {
		args = append(args, "--unit="+r.Unit)
	}
	if r.Since != "" {
		m := sinceRe.FindStringSubmatch(r.Since)
		args = append(args, "--since=-"+m[1]+sinceUnits[m[2]])
	}
	return args
}

func (h Host) logsRead(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var req LogsRequest
	if err := decode(raw, &req); err != nil {
		return nil, err
	}
	if err := req.validate(); err != nil {
		return nil, err
	}
	m := newMatcher(req.Grep, req.Lines)
	if err := h.Stream(ctx, m, "journalctl", req.journalArgs()...); err != nil {
		return nil, err
	}
	return LogsReply{Lines: m.lines()}, nil
}

// auditLogs names what was read, not what was searched for.
func auditLogs(raw json.RawMessage) []any {
	var r LogsRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"source", r.Source, "unit", r.Unit, "lines", r.Lines}
}

// maxLineBytes is how much of one journal line is kept. A longer line is still
// searched in full, and the kept part ends with a truncation note.
const maxLineBytes = 16 << 10

// matcher keeps the last n lines that contain needle (any line when empty) while
// journalctl writes into it, so memory stays near n × maxLineBytes whatever the
// output size or line length. Matching ignores ASCII case.
type matcher struct {
	needle []byte // lower-cased
	n      int
	keep   []string

	// The line being written.
	kept    []byte // its first maxLineBytes
	dropped int    // bytes after that
	found   bool   // needle seen anywhere in it
	tail    []byte // last len(needle)-1 lower-cased bytes, for matches across writes
}

func newMatcher(needle string, n int) *matcher {
	return &matcher{needle: lowerASCII([]byte(needle)), n: n}
}

// Write implements io.Writer.
func (m *matcher) Write(p []byte) (int, error) {
	total := len(p)
	for len(p) > 0 {
		i := bytes.IndexByte(p, '\n')
		seg := p
		if i >= 0 {
			seg = p[:i]
		}
		m.add(seg)
		if i < 0 {
			break
		}
		m.endLine()
		p = p[i+1:]
	}
	return total, nil
}

// add appends part of the current line: keep what fits, search all of it.
func (m *matcher) add(seg []byte) {
	room := maxLineBytes - len(m.kept)
	if room > 0 {
		m.kept = append(m.kept, seg[:min(len(seg), room)]...)
	}
	m.dropped += max(0, len(seg)-max(room, 0))
	if len(m.needle) == 0 || m.found {
		return
	}
	window := append(m.tail, lowerASCII(seg)...)
	if bytes.Contains(window, m.needle) {
		m.found = true
		return
	}
	keep := min(len(window), len(m.needle)-1)
	m.tail = append(m.tail[:0], window[len(window)-keep:]...)
}

func (m *matcher) endLine() {
	if len(m.needle) == 0 || m.found {
		line := string(m.kept)
		if m.dropped > 0 {
			line += fmt.Sprintf(" …[truncated %d bytes]", m.dropped)
		}
		m.keep = append(m.keep, line)
		if len(m.keep) >= 2*m.n {
			m.keep = append(m.keep[:0], m.keep[len(m.keep)-m.n:]...)
		}
	}
	m.kept, m.dropped, m.found, m.tail = m.kept[:0], 0, false, m.tail[:0]
}

// lines returns the kept lines, including an unterminated last one.
func (m *matcher) lines() []string {
	if len(m.kept) > 0 || m.dropped > 0 {
		m.endLine()
	}
	if len(m.keep) > m.n {
		m.keep = m.keep[len(m.keep)-m.n:]
	}
	if m.keep == nil {
		return []string{}
	}
	return m.keep
}

// lowerASCII lower-cases A-Z only, so a multi-byte character split across
// writes is never altered.
func lowerASCII(b []byte) []byte {
	out := make([]byte, len(b))
	for i, c := range b {
		if 'A' <= c && c <= 'Z' {
			c += 'a' - 'A'
		}
		out[i] = c
	}
	return out
}
