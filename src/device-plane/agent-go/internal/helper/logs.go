package helper

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
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
	// Grep keeps lines that contain this text, ignoring case (optional).
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
	out, err := h.Run(ctx, "journalctl", req.journalArgs()...)
	if err != nil {
		return nil, err
	}
	return LogsReply{Lines: lastMatching(out, req.Grep, req.Lines)}, nil
}

// lastMatching returns the last n lines that contain needle (any line when empty).
func lastMatching(out []byte, needle string, n int) []string {
	needle = strings.ToLower(needle)
	var keep []string
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 64<<10), 1<<20)
	for sc.Scan() {
		line := sc.Text()
		if needle != "" && !strings.Contains(strings.ToLower(line), needle) {
			continue
		}
		keep = append(keep, line)
		if len(keep) > 2*n {
			keep = append(keep[:0], keep[len(keep)-n:]...)
		}
	}
	if len(keep) > n {
		keep = keep[len(keep)-n:]
	}
	if keep == nil {
		keep = []string{}
	}
	return keep
}
