package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	"meteorcloud/meteorcli/internal/localrpc"
)

// Log sources: the journal, systemd units, Docker containers and a few files
// under /var/log, listed and printed read-only. This is the Python
// meteorcli-logs helper: every source must be one that logs.list reports right
// now, nothing from the caller reaches a shell, and file paths come from a
// fixed allowlist. `meteorcli logs` and the console's Logs window (over SSH,
// through /usr/local/libexec/meteorcli-logs) both end here.

// Limits of the Python helper.
const (
	maxSources = 300
	// logsListTimeout bounds each listing tool.
	logsListTimeout = 15 * time.Second
)

// defaultLogGlobs are the files that may be read (glob's * stops at "/").
var defaultLogGlobs = []string{"/var/log/nginx/*.log", "/var/log/syslog", "/var/log/messages", "/var/log/kern.log"}

// Source names, as the Python helper accepted them. Neither can start with "-".
var (
	unitRe         = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,199}\.service$`)
	logContainerRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)
)

// LogSource is one entry of logs.list, in the Python helper's JSON shape (the
// control plane parses it).
type LogSource struct {
	Kind    string `json:"kind"`
	Name    string `json:"name"`
	State   string `json:"state,omitempty"`
	Project string `json:"project,omitempty"`
	Size    *int64 `json:"size,omitempty"`
}

// LogsListReply is the answer of logs.list.
type LogsListReply struct {
	Sources []LogSource `json:"sources"`
}

// ShowLogsRequest is the params of logs.show.
type ShowLogsRequest struct {
	// Kind is journal, systemd, docker or file.
	Kind string `json:"kind"`
	// Name is the unit, container or file path (empty for journal).
	Name string `json:"name"`
	// Project, for a docker container, must be its compose project.
	Project string `json:"project"`
	// Lines is how many lines to print first, 1 to 10000 (default 200).
	Lines int `json:"lines"`
	// Since is 15m, 1h, 7d ... (not for files).
	Since string `json:"since"`
	// Grep keeps lines that contain this text, ignoring ASCII case.
	Grep string `json:"grep"`
	// Follow keeps printing new lines until the caller hangs up.
	Follow bool `json:"follow"`
}

// Validate checks the request's shape (not yet whether the source exists).
func (r *ShowLogsRequest) Validate() error {
	bad := func(format string, args ...any) error {
		return localrpc.Errorf(localrpc.CodeBadRequest, format, args...)
	}
	switch r.Kind {
	case "journal":
		if r.Name != "" {
			return bad("journal has no name")
		}
	case "systemd":
		if !unitRe.MatchString(r.Name) {
			return bad("invalid systemd name")
		}
	case "docker":
		if !logContainerRe.MatchString(r.Name) {
			return bad("invalid docker name")
		}
	case "file":
		if r.Name == "" || strings.HasPrefix(r.Name, "-") {
			return bad("missing source name")
		}
	default:
		return bad("unknown source kind")
	}
	if r.Project != "" && (r.Kind != "docker" || !logContainerRe.MatchString(r.Project)) {
		return bad("project needs a docker container and a compose project name")
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
		return bad("grep must be 1..%d printable characters", maxGrep)
	}
	return nil
}

func (h Host) logGlobs() []string {
	if h.LogGlobs != nil {
		return h.LogGlobs
	}
	return defaultLogGlobs
}

func (h Host) hasJournal() bool {
	dir := h.SystemdDir
	if dir == "" {
		dir = "/run/systemd/system"
	}
	st, err := os.Stat(dir)
	return err == nil && st.IsDir()
}

func (h Host) hasDocker() bool {
	sock := h.DockerSocket
	if sock == "" {
		sock = "/var/run/docker.sock"
	}
	_, err := os.Stat(sock)
	return err == nil
}

// lines runs a listing tool; a failure gives no lines, like the Python helper.
func (h Host) lines(ctx context.Context, name string, args ...string) []string {
	ctx, cancel := context.WithTimeout(ctx, logsListTimeout)
	defer cancel()
	out, err := h.Run(ctx, name, args...)
	if err != nil {
		return nil
	}
	return strings.Split(strings.TrimRight(string(out), "\n"), "\n")
}

func (h Host) systemdUnits(ctx context.Context) []LogSource {
	var out []LogSource
	for _, line := range h.lines(ctx, "systemctl", "list-units", "--type=service", "--all", "--plain", "--no-legend", "--no-pager") {
		// UNIT LOAD ACTIVE SUB DESCRIPTION; units that are not installed show LOAD=not-found.
		f := strings.Fields(line)
		if len(f) < 4 || f[1] != "loaded" || !unitRe.MatchString(f[0]) {
			continue
		}
		out = append(out, LogSource{Kind: "systemd", Name: f[0], State: f[2]})
	}
	return out
}

func (h Host) dockerContainers(ctx context.Context) []LogSource {
	if !h.hasDocker() {
		return nil
	}
	var out []LogSource
	format := `{{.Names}}	{{.State}}	{{.Label "com.docker.compose.project"}}`
	for _, line := range h.engineLines(ctx, "ps", "--all", "--format", format) {
		name, rest, _ := strings.Cut(line, "\t")
		state, project, _ := strings.Cut(rest, "\t")
		if !logContainerRe.MatchString(name) {
			continue
		}
		s := LogSource{Kind: "docker", Name: name, State: state}
		if logContainerRe.MatchString(project) {
			s.Project = project
		}
		out = append(out, s)
	}
	return out
}

func (h Host) logFiles() []LogSource {
	var out []LogSource
	for _, pattern := range h.logGlobs() {
		matches, _ := filepath.Glob(pattern)
		sort.Strings(matches)
		for _, path := range matches {
			if st, ok := safeLogFile(path); ok {
				size := st.Size()
				out = append(out, LogSource{Kind: "file", Name: path, Size: &size})
			}
		}
	}
	return out
}

// safeLogFile accepts a regular file reached without a symlink.
func safeLogFile(path string) (os.FileInfo, bool) {
	st, err := os.Lstat(path)
	if err != nil || !st.Mode().IsRegular() {
		return nil, false
	}
	if real, err := filepath.EvalSymlinks(path); err != nil || real != path {
		return nil, false
	}
	return st, true
}

// ListLogs returns the sources this device has right now (at most 300).
func ListLogs(ctx context.Context, h Host) []LogSource {
	sources := []LogSource{}
	if h.hasJournal() {
		sources = append(sources, LogSource{Kind: "journal", Name: ""})
		sources = append(sources, h.systemdUnits(ctx)...)
	}
	sources = append(sources, h.dockerContainers(ctx)...)
	sources = append(sources, h.logFiles()...)
	if len(sources) > maxSources {
		sources = sources[:maxSources]
	}
	return sources
}

// requireListed refuses a source that logs.list does not report right now.
func (h Host) requireListed(ctx context.Context, r ShowLogsRequest) error {
	bad := func(format string, args ...any) error {
		return localrpc.Errorf(localrpc.CodeBadRequest, format, args...)
	}
	switch r.Kind {
	case "journal":
		if !h.hasJournal() {
			return bad("this device has no systemd journal")
		}
		return nil
	case "file":
		for _, s := range h.logFiles() {
			if s.Name == r.Name {
				return nil
			}
		}
		return bad("not an allowed log file")
	}
	found := h.systemdUnits(ctx)
	if r.Kind == "docker" {
		found = h.dockerContainers(ctx)
	}
	for _, s := range found {
		if s.Name != r.Name {
			continue
		}
		if r.Project != "" && s.Project != r.Project {
			return bad("container %s is not part of compose project %s", r.Name, r.Project)
		}
		return nil
	}
	return bad("%s source not found: %s", r.Kind, r.Name)
}

// showCommand is the fixed command for one source. lines may be 0 (follow
// only new lines).
func showCommand(r ShowLogsRequest, lines int, follow bool) (string, []string) {
	switch r.Kind {
	case "journal", "systemd":
		args := []string{"--no-pager", "--quiet", "--output=short-iso", "--lines=" + strconv.Itoa(lines)}
		if r.Kind == "systemd" {
			args = append(args, "--unit="+r.Name)
		}
		if r.Since != "" {
			m := sinceRe.FindStringSubmatch(r.Since)
			args = append(args, "--since=-"+m[1]+sinceUnits[m[2]])
		}
		if follow {
			args = append(args, "--follow")
		}
		return "journalctl", args
	case "docker":
		args := []string{"logs", "--timestamps", "--tail", strconv.Itoa(lines)}
		if r.Since != "" {
			m := sinceRe.FindStringSubmatch(r.Since)
			n, _ := strconv.Atoi(m[1])
			args = append(args, "--since", strconv.Itoa(n*sinceSeconds[m[2]])+"s")
		}
		if follow {
			args = append(args, "--follow")
		}
		return "docker", append(args, "--", r.Name)
	}
	// tail cannot filter by time; since does not apply to files.
	args := []string{"-n", strconv.Itoa(lines)}
	if follow {
		args = append(args, "-F")
	}
	return "tail", append(args, "--", r.Name)
}

var sinceSeconds = map[string]int{"m": 60, "h": 3600, "d": 86400}

// ShowLogs writes the source's lines to w. With Grep it first prints the last
// Lines matches of recent history, then (with Follow) new matching lines.
func ShowLogs(ctx context.Context, h Host, r ShowLogsRequest, w io.Writer) error {
	if err := r.Validate(); err != nil {
		return err
	}
	if err := h.requireListed(ctx, r); err != nil {
		return err
	}
	if r.Grep == "" {
		name, args := showCommand(r, r.Lines, r.Follow)
		return h.StreamAll(ctx, w, name, args...)
	}
	m := newMatcher(r.Grep, r.Lines)
	name, args := showCommand(r, grepScanLines, false)
	if err := h.StreamAll(ctx, m, name, args...); err != nil {
		return err
	}
	for _, line := range m.lines() {
		if _, err := io.WriteString(w, line+"\n"); err != nil {
			return err
		}
	}
	if !r.Follow {
		return nil
	}
	name, args = showCommand(r, 0, true)
	return h.StreamAll(ctx, &grepWriter{needle: lowerASCII([]byte(r.Grep)), w: w}, name, args...)
}

// grepWriter passes on the lines that contain needle (lower-cased).
type grepWriter struct {
	needle  []byte // lower-cased
	w       io.Writer
	partial []byte // the line's first maxLineBytes, for display
	tail    []byte // the line's last len(needle)-1 bytes, lower-cased: a match across writes
	matched bool   // the line so far contains needle (searched in full, not only partial)
}

func (g *grepWriter) Write(p []byte) (int, error) {
	n := len(p)
	for len(p) > 0 {
		i := bytes.IndexByte(p, '\n')
		seg := p
		if i >= 0 {
			seg = p[:i]
		}
		g.scan(seg)
		if len(g.partial) < maxLineBytes {
			g.partial = append(g.partial, seg[:min(len(seg), maxLineBytes-len(g.partial))]...)
		}
		if i < 0 {
			break
		}
		if g.matched {
			if _, err := g.w.Write(append(g.partial, '\n')); err != nil {
				return 0, err
			}
		}
		g.partial, g.tail, g.matched = g.partial[:0], g.tail[:0], false
		p = p[i+1:]
	}
	return n, nil
}

// scan searches the next piece of the current line, joined to the end of the
// previous piece so a match split between writes is found.
func (g *grepWriter) scan(seg []byte) {
	if g.matched || len(seg) == 0 {
		return
	}
	window := append(append([]byte(nil), g.tail...), lowerASCII(seg)...)
	if bytes.Contains(window, g.needle) {
		g.matched = true
		return
	}
	keep := max(len(g.needle)-1, 0)
	if len(window) > keep {
		window = window[len(window)-keep:]
	}
	g.tail = append(g.tail[:0], window...)
}

func (h Host) logsList(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	if err := decode(raw, &struct{}{}); err != nil {
		return nil, err
	}
	return LogsListReply{Sources: ListLogs(ctx, h)}, nil
}

func (h Host) logsShow(ctx context.Context, _ localrpc.Peer, raw json.RawMessage, w io.Writer) error {
	var r ShowLogsRequest
	if err := decode(raw, &r); err != nil {
		return err
	}
	return ShowLogs(ctx, h, r, w)
}

// auditShow names the source, not what was searched for.
func auditShow(raw json.RawMessage) []any {
	var r ShowLogsRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"kind", r.Kind, "name", r.Name, "lines", r.Lines, "follow", r.Follow}
}
