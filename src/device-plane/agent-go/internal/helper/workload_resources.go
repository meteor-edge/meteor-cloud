package helper

import (
	"context"
	"encoding/json"
	"math"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"meteorcloud/meteorcli/internal/localrpc"
)

// What workloads use (the console's software page): live container readings,
// and Docker's disk space. Both only read.

// WorkloadStats is one running container's use right now.
type WorkloadStats struct {
	Name          string  `json:"name"`
	CPUPercent    float64 `json:"cpu_percent"`
	MemoryBytes   int64   `json:"memory_bytes"`
	MemoryPercent float64 `json:"memory_percent"`
	NetRxBytes    int64   `json:"net_rx_bytes"`
	NetTxBytes    int64   `json:"net_tx_bytes"`
	BlockRead     int64   `json:"block_read_bytes"`
	BlockWrite    int64   `json:"block_write_bytes"`
	PIDs          int     `json:"pids"`
}

// dockerStat is a line of docker stats --format '{{json .}}'.
type dockerStat struct {
	Name, CPUPerc, MemUsage, MemPerc, NetIO, BlockIO, PIDs string
}

// DiskUse is one line of docker system df: totals only, no names.
type DiskUse struct {
	Type        string `json:"type"`
	Total       int    `json:"total"`
	Active      int    `json:"active"`
	Size        int64  `json:"size"`
	Reclaimable int64  `json:"reclaimable"`
}

// DockerDiskRequest is the params of docker.disk: the volumes and containers
// (full ids) to measure.
type DockerDiskRequest struct {
	Volumes    []string `json:"volumes"`
	Containers []string `json:"containers"`
}

// DockerDisk is the answer of docker.disk.
type DockerDisk struct {
	Items      []DiskUse        `json:"items"`
	Volumes    map[string]int64 `json:"volumes"`
	Containers map[string]int64 `json:"containers"`
}

const maxDiskVolumes = 256

var containerIDRe = regexp.MustCompile(`^[0-9a-f]{64}$`)

// workloadStats answers for the workload's own running containers only.
func (h Host) workloadStats(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r WorkloadRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if err := r.validate(); err != nil {
		return nil, err
	}
	out := []WorkloadStats{}
	if r.Kind == "systemd" {
		return out, nil
	}
	ids, err := h.containerIDs(ctx, r)
	if err != nil {
		return nil, err
	}
	var running []string
	for _, c := range h.containers(ctx, ids[:min(len(ids), MaxContainers)]) {
		if c.Running && c.ID != "" {
			running = append(running, c.ID)
		}
	}
	if len(running) == 0 {
		return out, nil
	}
	lines, err := h.dockerLines(ctx, append([]string{"stats", "--no-stream", "--no-trunc", "--format", "{{json .}}"}, running...)...)
	if err != nil {
		return nil, err
	}
	for _, l := range lines {
		var s dockerStat
		if json.Unmarshal([]byte(l), &s) != nil || s.Name == "" {
			continue
		}
		mem, _, _ := strings.Cut(s.MemUsage, "/")
		rx, tx, _ := strings.Cut(s.NetIO, "/")
		read, write, _ := strings.Cut(s.BlockIO, "/")
		w := WorkloadStats{Name: s.Name, CPUPercent: percent(s.CPUPerc), MemoryBytes: size(mem), MemoryPercent: percent(s.MemPerc),
			NetRxBytes: size(rx), NetTxBytes: size(tx), BlockRead: size(read), BlockWrite: size(write)}
		w.PIDs, _ = strconv.Atoi(s.PIDs)
		out = append(out, w)
	}
	return out, nil
}

// dockerDisk measures Docker's disk space, and the named volumes. Measuring
// volumes walks their files, so callers ask rarely.
func (h Host) dockerDisk(ctx context.Context, _ localrpc.Peer, raw json.RawMessage) (any, error) {
	var r DockerDiskRequest
	if err := decode(raw, &r); err != nil {
		return nil, err
	}
	if len(r.Volumes) > maxDiskVolumes || len(r.Containers) > maxDiskVolumes {
		return nil, badRequest("at most %d volumes and containers", maxDiskVolumes)
	}
	for _, id := range r.Containers {
		if !containerIDRe.MatchString(id) {
			return nil, badRequest("%q is not a container id", id)
		}
	}
	out := DockerDisk{Items: []DiskUse{}, Volumes: map[string]int64{}, Containers: map[string]int64{}}
	lines, err := h.dockerLines(ctx, "system", "df", "--format", "{{json .}}")
	if err != nil {
		return nil, err
	}
	for _, l := range lines {
		var d struct{ Type, TotalCount, Active, Size, Reclaimable string }
		if json.Unmarshal([]byte(l), &d) != nil || d.Type == "" {
			continue
		}
		u := DiskUse{Type: d.Type, Size: size(d.Size), Reclaimable: size(d.Reclaimable)}
		u.Total, _ = strconv.Atoi(d.TotalCount)
		u.Active, _ = strconv.Atoi(d.Active)
		out.Items = append(out.Items, u)
	}
	if len(r.Containers) > 0 {
		args := []string{"ps", "-a", "--no-trunc", "--size", "--format", "{{.ID}} {{.Size}}"}
		for _, id := range r.Containers {
			args = append(args, "--filter", "id="+id)
		}
		// "<id> 1.09kB (virtual 53MB)". Without it the rest is still good.
		lines, _ := h.dockerLines(ctx, args...)
		for _, l := range lines {
			if id, v, ok := strings.Cut(l, " "); ok && slices.Contains(r.Containers, id) {
				out.Containers[id] = size(v)
			}
		}
	}
	if len(r.Volumes) == 0 {
		return out, nil
	}
	// Names and sizes only; what was not asked for is dropped here.
	lines, err = h.dockerLines(ctx, "system", "df", "-v", "--format", `{{range .Volumes}}{{.Name}} {{.Size}}{{println}}{{end}}`)
	if err != nil {
		return out, nil // the totals are still good
	}
	for _, l := range lines {
		if name, v, ok := strings.Cut(l, " "); ok && slices.Contains(r.Volumes, name) && v != "N/A" {
			out.Volumes[name] = size(v)
		}
	}
	return out, nil
}

func percent(v string) float64 {
	f, err := strconv.ParseFloat(strings.TrimSuffix(strings.TrimSpace(v), "%"), 64)
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
		return 0
	}
	return f
}

// sizeUnits are docker's suffixes, longest first so "mib" wins over "b".
var sizeUnits = []struct {
	suffix string
	factor float64
}{
	{"kib", 1 << 10}, {"mib", 1 << 20}, {"gib", 1 << 30}, {"tib", 1 << 40},
	{"kb", 1e3}, {"mb", 1e6}, {"gb", 1e9}, {"tb", 1e12}, {"b", 1},
}

// size reads "12.24GB (65%)", "101.1MiB" or "0B"; 0 for anything else.
func size(v string) int64 {
	v, _, _ = strings.Cut(strings.ToLower(strings.TrimSpace(v)), " ")
	for _, u := range sizeUnits {
		if n, ok := strings.CutSuffix(v, u.suffix); ok {
			f, err := strconv.ParseFloat(n, 64)
			if err != nil || f < 0 || f*u.factor > 1<<60 {
				return 0
			}
			return int64(math.Round(f * u.factor))
		}
	}
	return 0
}
