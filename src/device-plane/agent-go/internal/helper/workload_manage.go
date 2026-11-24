package helper

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"

	"meteorcloud/meteorcli/internal/config"
	"meteorcloud/meteorcli/internal/localrpc"
)

// Looking at and managing deployed workloads (internal/workloads and the
// workload_* actions). The agent names a workload; the helper decides what
// belongs to it, at call time, from marks only root can have made:
//
//   - docker: the label docker.run sets (LabelWorkload);
//   - docker_compose: Compose's own labels, when the project was brought up
//     from the project's directory under the agent's compose directory;
//   - systemd: a record systemd.install_unit left in the helper's StateDir.
//
// Everything else on the device (the broker, a container the customer runs by
// hand, a unit from the distribution) can be neither read nor changed here.

// Labels on containers.
const (
	LabelWorkload       = "meteorcloud.workload"
	LabelRelease        = "meteorcloud.release"
	labelComposeProject = "com.docker.compose.project"
	labelComposeService = "com.docker.compose.service"
	labelComposeDir     = "com.docker.compose.project.working_dir"
)

// labelRe is a label value the helper sets or filters on.
var labelRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$`)

// ValidLabel reports whether v can be a workload or release label.
func ValidLabel(v string) bool { return labelRe.MatchString(v) }

// unitsDir is where StateDir keeps one empty file per installed unit.
const unitsDir = "units"

// WorkloadRequest names one deployed workload.
type WorkloadRequest struct {
	// Kind is systemd, docker or docker_compose.
	Kind string `json:"kind"`
	// Key is the workload_key (docker: the label's value).
	Key string `json:"key"`
	// Name is the container, the compose project or the unit.
	Name string `json:"name"`
}

func (r *WorkloadRequest) validate() error {
	switch r.Kind {
	case "docker":
		if !labelRe.MatchString(r.Key) || !containerRe.MatchString(r.Name) {
			return badRequest("not a docker workload: %q, %q", r.Key, r.Name)
		}
	case "docker_compose":
		if !projectRe.MatchString(r.Name) {
			return badRequest("%q is not a compose project name", r.Name)
		}
	case "systemd":
		if !validName.MatchString(r.Name) || !strings.HasSuffix(r.Name, ".service") {
			return badRequest("unit must be a .service name of a-z, 0-9, '.', '_' or '-'")
		}
		if strings.HasPrefix(r.Name, "meteorcli") {
			return badRequest("meteorcli units are not workloads")
		}
	default:
		return badRequest("%q is not a workload kind", r.Kind)
	}
	return nil
}

// containerTemplate prints one JSON object per container: fixed fields only,
// never Config.Env, the command or labels that are not ours or Compose's.
// `index` on a struct fails, so docker runs the template on the raw JSON, where
// a key that is absent (Name of a bind mount, Health) must be read with index.
const containerTemplate = `{"id":{{json .Id}},"name":{{json .Name}},` +
	`"service":{{with .Config.Labels}}{{json (index . "` + labelComposeService + `")}}{{else}}""{{end}},` +
	`"release":{{with .Config.Labels}}{{json (index . "` + LabelRelease + `")}}{{else}}""{{end}},` +
	`"state":{{json .State.Status}},"running":{{json .State.Running}},` +
	`"health":{{with index .State "Health"}}{{json .Status}}{{else}}""{{end}},` +
	`"restarts":{{json .RestartCount}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},` +
	`"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},` +
	`"image":{{json .Config.Image}},"image_id":{{json .Image}},` +
	`"restart_policy":{{with .HostConfig.RestartPolicy}}{{json .Name}}{{else}}""{{end}},` +
	`"memory_limit":{{json .HostConfig.Memory}},"nano_cpus":{{json .HostConfig.NanoCpus}},` +
	`"port_map":{{json (index .NetworkSettings "Ports")}},` +
	`"mounts":[{{range $i, $m := .Mounts}}{{if $i}},{{end}}{"type":{{json (index $m "Type")}},"name":{{json (index $m "Name")}},` +
	`"source":{{json (index $m "Source")}},"destination":{{json (index $m "Destination")}},"rw":{{json (index $m "RW")}}}{{end}}],` +
	`"networks":[{{range $n, $c := index .NetworkSettings "Networks"}}{{json $n}},{{end}}""]}`

const (
	imageTemplate   = `{"id":{{json .Id}},"tags":{{json .RepoTags}},"digests":{{json .RepoDigests}},"size":{{json .Size}},"created":{{json .Created}}}`
	volumeTemplate  = `{"name":{{json .Name}},"driver":{{json .Driver}}}`
	networkTemplate = `{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},"internal":{{json .Internal}},` +
		`"subnets":[{{range .IPAM.Config}}{{json .Subnet}},{{end}}""],"gateways":[{{range .IPAM.Config}}{{json .Gateway}},{{end}}""]}`
)

// unitProperties are what unit status shows (never Environment= or ExecStart=).
var unitProperties = []string{"LoadState", "ActiveState", "SubState", "UnitFileState", "Result", "NRestarts", "ExecMainStatus",
	"MemoryCurrent", "CPUUsageNSec", "ActiveEnterTimestamp", "InactiveEnterTimestamp"}

// WorkloadPort is one published or exposed port.
type WorkloadPort struct {
	HostIP    string `json:"host_ip,omitempty"`
	HostPort  string `json:"host_port,omitempty"`
	Container string `json:"container"`
}

// WorkloadMount is one mount (paths only).
type WorkloadMount struct {
	Type        string `json:"type"`
	Name        string `json:"name,omitempty"`
	Source      string `json:"source,omitempty"`
	Destination string `json:"destination"`
	RW          bool   `json:"rw"`
}

// WorkloadContainer is one container of a workload.
type WorkloadContainer struct {
	ID         string          `json:"id"`
	Name       string          `json:"name"`
	Service    string          `json:"service"`
	Release    string          `json:"release"`
	State      string          `json:"state"`
	Running    bool            `json:"running"`
	Health     string          `json:"health"`
	Restarts   int             `json:"restarts"`
	ExitCode   int             `json:"exit_code"`
	OOMKilled  bool            `json:"oom_killed"`
	StartedAt  string          `json:"started_at"`
	FinishedAt string          `json:"finished_at"`
	Image      string          `json:"image"`
	ImageID    string          `json:"image_id"`
	Ports      []WorkloadPort  `json:"ports"`
	Mounts     []WorkloadMount `json:"mounts"`
	Networks   []string        `json:"networks"`
	// 0: no limit.
	RestartPolicy string `json:"restart_policy"`
	MemoryLimit   int64  `json:"memory_limit"`
	NanoCPUs      int64  `json:"nano_cpus"`
}

// WorkloadImage, WorkloadVolume and WorkloadNetwork are objects the workload's
// containers use.
type WorkloadImage struct {
	ID      string   `json:"id"`
	Tags    []string `json:"tags"`
	Digests []string `json:"digests"`
	Size    int64    `json:"size"`
	Created string   `json:"created"`
}

type WorkloadVolume struct {
	Name   string `json:"name"`
	Driver string `json:"driver"`
	// Size is set by the agent, nil until it was measured.
	Size *int64 `json:"size"`
}

type WorkloadNetwork struct {
	Name     string   `json:"name"`
	Driver   string   `json:"driver"`
	Scope    string   `json:"scope"`
	Internal bool     `json:"internal"`
	Subnets  []string `json:"subnets"`
	Gateways []string `json:"gateways"`
}

// WorkloadUnit is a systemd unit's state. MemoryBytes and CPUSeconds are nil
// without accounting.
type WorkloadUnit struct {
	Name        string   `json:"name"`
	LoadState   string   `json:"load_state"`
	ActiveState string   `json:"active_state"`
	SubState    string   `json:"sub_state"`
	Enabled     string   `json:"enabled"`
	Result      string   `json:"result"`
	Restarts    int      `json:"restarts"`
	ExitStatus  int      `json:"exit_status"`
	MemoryBytes *int64   `json:"memory_bytes"`
	CPUSeconds  *float64 `json:"cpu_seconds"`
	ActiveSince string   `json:"active_since"`
	StoppedAt   string   `json:"stopped_at"`
}

// WorkloadState is the answer of workload.inspect. Managed is false when the
// helper cannot prove the workload is MeteorCloud's (deployed by an older
// agent): workload.control would refuse it.
type WorkloadState struct {
	Managed    bool                `json:"managed"`
	Truncated  bool                `json:"truncated"`
	Containers []WorkloadContainer `json:"containers"`
	Images     []WorkloadImage     `json:"images"`
	Volumes    []WorkloadVolume    `json:"volumes"`
	Networks   []WorkloadNetwork   `json:"networks"`
	Unit       *WorkloadUnit       `json:"unit"`
}

// composeFilters selects the containers Compose made from the project's own
// directory under the agent's compose directory.
func (h Host) composeFilters(project string) ([]string, error) {
	dir, err := filepath.EvalSymlinks(h.ComposeDir)
	if err != nil {
		return nil, err
	}
	return []string{"--filter", "label=" + labelComposeProject + "=" + project,
		"--filter", "label=" + labelComposeDir + "=" + filepath.Join(dir, project)}, nil
}

// dockerLines runs docker and returns its stdout's lines.
func (h Host) dockerLines(ctx context.Context, args ...string) ([]string, error) {
	out := &capped{max: maxStdout}
	if err := h.Stream(ctx, out, "docker", args...); err != nil {
		return nil, err
	}
	var lines []string
	for _, l := range strings.Split(out.String(), "\n") {
		if l = strings.TrimSpace(l); l != "" {
			lines = append(lines, l)
		}
	}
	return lines, nil
}

// containerIDs returns the containers of a docker or compose workload, all
// states, newest first.
func (h Host) containerIDs(ctx context.Context, r WorkloadRequest) ([]string, error) {
	args := []string{"ps", "-a", "-q", "--no-trunc"}
	if r.Kind == "docker" {
		args = append(args, "--filter", "label="+LabelWorkload+"="+r.Key)
	} else {
		filters, err := h.composeFilters(r.Name)
		if err != nil {
			return nil, err
		}
		args = append(args, filters...)
	}
	return h.dockerLines(ctx, args...)
}

// inspectLines decodes `docker <what> inspect` output, one JSON object per
// line. Objects that vanished meanwhile are skipped, like docker.inspect.
func inspectLines[T any](ctx context.Context, h Host, format string, names []string, what ...string) []T {
	out := []T{}
	if len(names) == 0 {
		return out
	}
	args := append(slices.Clone(what), "inspect", "-f", format)
	buf := &capped{max: maxStdout}
	_ = h.Stream(ctx, buf, "docker", append(args, names...)...)
	for _, line := range bytes.Split(buf.Bytes(), []byte("\n")) {
		var v T
		if len(bytes.TrimSpace(line)) > 0 && json.Unmarshal(line, &v) == nil {
			out = append(out, v)
		}
	}
	return out
}

// inspected is a containerTemplate line before its ports are flattened.
type inspected struct {
	WorkloadContainer
	PortMap map[string][]struct {
		HostIP   string `json:"HostIp"`
		HostPort string `json:"HostPort"`
	} `json:"port_map"`
}

func (h Host) containers(ctx context.Context, ids []string) []WorkloadContainer {
	out := []WorkloadContainer{}
	for _, c := range inspectLines[inspected](ctx, h, containerTemplate, ids, "container") {
		w := c.WorkloadContainer
		w.Name = strings.TrimPrefix(w.Name, "/")
		w.Networks = slices.DeleteFunc(w.Networks, func(n string) bool { return n == "" })
		w.Ports = []WorkloadPort{}
		for port, binds := range c.PortMap {
			if len(binds) == 0 {
				w.Ports = append(w.Ports, WorkloadPort{Container: port})
			}
			for _, b := range binds {
				w.Ports = append(w.Ports, WorkloadPort{HostIP: b.HostIP, HostPort: b.HostPort, Container: port})
			}
		}
		sort.Slice(w.Ports, func(i, j int) bool {
			a, b := w.Ports[i], w.Ports[j]
			return a.Container+"|"+a.HostIP+"|"+a.HostPort < b.Container+"|"+b.HostIP+"|"+b.HostPort
		})
		out = append(out, w)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func (h Host) workloadInspect(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WorkloadRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := r.validate(); err != nil {
		return nil, err
	}
	s := WorkloadState{Containers: []WorkloadContainer{}, Images: []WorkloadImage{}, Volumes: []WorkloadVolume{}, Networks: []WorkloadNetwork{}}
	if r.Kind == "systemd" {
		unit, err := h.unitStatus(ctx, r.Name)
		if err != nil {
			return nil, err
		}
		s.Unit, s.Managed = unit, h.ownsUnit(r.Name)
		return s, nil
	}
	ids, err := h.containerIDs(ctx, r)
	if err != nil {
		return nil, err
	}
	s.Managed = len(ids) > 0
	if len(ids) > MaxContainers {
		ids, s.Truncated = ids[:MaxContainers], true
	}
	s.Containers = h.containers(ctx, ids)
	images, volumes, networks := set{}, set{}, set{}
	for _, c := range s.Containers {
		images.add(c.ImageID)
		for _, m := range c.Mounts {
			if m.Type == "volume" {
				volumes.add(m.Name)
			}
		}
		for _, n := range c.Networks {
			networks.add(n)
		}
	}
	s.Images = inspectLines[WorkloadImage](ctx, h, imageTemplate, images.sorted(), "image")
	s.Volumes = inspectLines[WorkloadVolume](ctx, h, volumeTemplate, volumes.sorted(), "volume")
	s.Networks = inspectLines[WorkloadNetwork](ctx, h, networkTemplate, networks.sorted(), "network")
	empty := func(v string) bool { return v == "" }
	for i := range s.Networks {
		n := &s.Networks[i]
		n.Subnets, n.Gateways = slices.DeleteFunc(n.Subnets, empty), slices.DeleteFunc(n.Gateways, empty)
	}
	return s, nil
}

// set collects distinct non-empty names.
type set map[string]bool

func (s set) add(v string) {
	if v != "" && !strings.HasPrefix(v, "-") {
		s[v] = true
	}
}

func (s set) sorted() []string {
	out := make([]string, 0, len(s))
	for k := range s {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (h Host) unitStatus(ctx context.Context, name string) (*WorkloadUnit, error) {
	out, err := h.Run(ctx, "systemctl", "show", "--no-pager", "--property="+strings.Join(unitProperties, ","), "--", name)
	if err != nil {
		return nil, err
	}
	p := map[string]string{}
	for _, line := range strings.Split(string(out), "\n") {
		if k, v, ok := strings.Cut(strings.TrimSpace(line), "="); ok {
			p[k] = v
		}
	}
	u := &WorkloadUnit{Name: name, LoadState: p["LoadState"], ActiveState: p["ActiveState"], SubState: p["SubState"],
		Enabled: p["UnitFileState"], Result: p["Result"], ActiveSince: p["ActiveEnterTimestamp"], StoppedAt: p["InactiveEnterTimestamp"]}
	u.Restarts, _ = strconv.Atoi(p["NRestarts"])
	u.ExitStatus, _ = strconv.Atoi(p["ExecMainStatus"])
	// "[not set]" (or the maximum) without accounting.
	if n, err := strconv.ParseInt(p["MemoryCurrent"], 10, 64); err == nil {
		u.MemoryBytes = &n
	}
	if n, err := strconv.ParseInt(p["CPUUsageNSec"], 10, 64); err == nil {
		s := float64(n/1e6) / 1e3
		u.CPUSeconds = &s
	}
	return u, nil
}

// recordUnit remembers that install_unit wrote name; only root can.
func (h Host) recordUnit(name string) error {
	if h.StateDir == "" {
		return nil
	}
	dir := filepath.Join(h.StateDir, unitsDir)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	return config.WriteFileAtomic(filepath.Join(dir, name), nil, 0o600)
}

func (h Host) forgetUnit(name string) error {
	if h.StateDir == "" {
		return nil
	}
	if err := os.Remove(filepath.Join(h.StateDir, unitsDir, name)); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// ownsUnit reports whether install_unit wrote name and the file is still there.
func (h Host) ownsUnit(name string) bool {
	if h.StateDir == "" {
		return false
	}
	st, err := os.Lstat(filepath.Join(h.StateDir, unitsDir, name))
	if err != nil || !st.Mode().IsRegular() {
		return false
	}
	st, err = os.Lstat(filepath.Join(h.UnitDir, name))
	return err == nil && st.Mode().IsRegular()
}

// WorkloadControlRequest is the params of workload.control.
type WorkloadControlRequest struct {
	WorkloadRequest
	// Verb is restart, stop or start.
	Verb string `json:"verb"`
	// Service limits a compose workload to one of its services.
	Service string `json:"service,omitempty"`
}

func auditWorkloadControl(raw json.RawMessage) []any {
	var r WorkloadControlRequest
	_ = json.Unmarshal(raw, &r)
	return []any{"kind", r.Kind, "workload", r.Key, "name", r.Name, "service", r.Service, "verb", r.Verb}
}

func notManaged(what string) error {
	return localrpc.Errorf(localrpc.CodeBadRequest,
		"%s was not deployed by MeteorCloud, or by an older agent; deploy it again to manage it", what)
}

func (h Host) workloadControl(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WorkloadControlRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := r.validate(); err != nil {
		return nil, err
	}
	if r.Verb != "restart" && r.Verb != "stop" && r.Verb != "start" {
		return nil, badRequest("verb must be restart, stop or start")
	}
	if r.Service != "" && r.Kind != "docker_compose" {
		return nil, badRequest("service needs a docker_compose workload")
	}
	var err error
	switch r.Kind {
	case "systemd":
		err = h.controlUnit(ctx, r)
	case "docker":
		err = h.controlContainer(ctx, r)
	default:
		err = h.controlProject(ctx, r)
	}
	if err != nil {
		return nil, err
	}
	return struct{}{}, nil
}

// controlUnit: a stop is kept across reboots (disable), and undone by start.
func (h Host) controlUnit(ctx context.Context, r WorkloadControlRequest) error {
	if !h.ownsUnit(r.Name) {
		return notManaged("unit " + r.Name)
	}
	args := map[string][]string{
		"restart": {"restart"}, "stop": {"disable", "--now"}, "start": {"enable", "--now"},
	}[r.Verb]
	_, err := h.Run(ctx, "systemctl", append(slices.Clone(args), "--", r.Name)...)
	return err
}

// controlContainer acts on the container's id, so a container that takes the
// name meanwhile is not touched.
func (h Host) controlContainer(ctx context.Context, r WorkloadControlRequest) error {
	ids, err := h.containerIDs(ctx, r.WorkloadRequest)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return notManaged("container " + r.Name)
	}
	if len(ids) > MaxContainers {
		return badRequest("too many containers carry the label of %s", r.Key)
	}
	_, err = h.Run(ctx, "docker", append([]string{r.Verb}, ids...)...)
	return err
}

// controlProject goes through Compose, which knows the services' order.
// Compose selects by project name only, so a project that also has containers
// from another directory is refused.
func (h Host) controlProject(ctx context.Context, r WorkloadControlRequest) error {
	ids, err := h.containerIDs(ctx, r.WorkloadRequest)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return notManaged("compose project " + r.Name)
	}
	all, err := h.dockerLines(ctx, "ps", "-a", "-q", "--no-trunc", "--filter", "label="+labelComposeProject+"="+r.Name)
	if err != nil {
		return err
	}
	for _, id := range all {
		if !slices.Contains(ids, id) {
			return badRequest("compose project %s also has containers MeteorCloud did not deploy", r.Name)
		}
	}
	containers := h.containers(ctx, ids)
	known, running := set{}, set{}
	for _, c := range containers {
		known.add(c.Service)
		if c.Running {
			running.add(c.Service)
		}
	}
	args := []string{"compose", "-p", r.Name, r.Verb}
	if r.Verb == "restart" {
		// Not what depends on it: that may be stopped on purpose.
		args = append(args, "--no-deps")
	}
	switch {
	case r.Service != "":
		if !known[r.Service] {
			return badRequest("compose project %s has no service %q", r.Name, r.Service)
		}
		args = append(args, r.Service)
	case r.Verb == "restart":
		// Only what runs: a one-shot job that finished must not run again.
		// Without names Compose would restart everything.
		if len(running) == 0 {
			return badRequest("nothing of compose project %s runs; start it instead", r.Name)
		}
		args = append(args, running.sorted()...)
	}
	_, err = h.Run(ctx, "docker", args...)
	return err
}
