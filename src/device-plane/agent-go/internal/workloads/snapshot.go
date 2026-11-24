package workloads

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"meteorcloud/meteorcli/internal/agent"
	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/bus"
	"meteorcloud/meteorcli/internal/deploy"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/metrics"
	"meteorcloud/meteorcli/internal/workload"
)

// caller is the part of the helper client this package uses.
type caller interface {
	Call(ctx context.Context, op string, params, out any) error
}

// maxSnapshot is the contract's limit of one snapshot's JSON.
const maxSnapshot = 64 << 10

// Limits of the lists in a snapshot (software-snapshots.request.json).
const (
	maxPorts    = 64
	maxMounts   = 64
	maxNames    = 32 // networks per container, tags and digests per image
	maxSubnets  = 8
	maxObjects  = 64 // images, volumes, networks
	maxText     = 512
	maxServices = 64
)

// container is a helper container plus the name its app.<name>.* metrics have.
type container struct {
	helper.WorkloadContainer
	Metric string `json:"metric"`
	// Stats is nil for a stopped container.
	Stats *helper.WorkloadStats `json:"stats"`
}

// Snapshot is what one workload is made of right now.
type Snapshot struct {
	WorkloadKey string `json:"workload_key"`
	Kind        string `json:"kind"`
	TakenAt     string `json:"taken_at"`
	// Managed is false when restart, stop and start would be refused (Note says why).
	Managed         bool                     `json:"managed"`
	Note            string                   `json:"note"`
	Truncated       bool                     `json:"truncated"`
	StoppedServices []string                 `json:"stopped_services"`
	Containers      []container              `json:"containers"`
	Images          []helper.WorkloadImage   `json:"images"`
	Volumes         []helper.WorkloadVolume  `json:"volumes"`
	Networks        []helper.WorkloadNetwork `json:"networks"`
	Unit            *helper.WorkloadUnit     `json:"unit"`
	// Disk is Docker's disk space on the whole device (totals, no names).
	Disk *Disk `json:"disk"`
}

const noteOlder = "Deployed by an older agent. Deploy it again to restart, stop or start it from here."

// Take asks the helper what in is made of.
func Take(ctx context.Context, h caller, in deploy.Installed, stop workload.Stop, now time.Time) (*Snapshot, error) {
	var st helper.WorkloadState
	req := helper.WorkloadRequest{Kind: in.Kind, Key: in.Key, Name: in.Name}
	if err := h.Call(ctx, "workload.inspect", req, &st); err != nil {
		return nil, err
	}
	s := &Snapshot{WorkloadKey: in.Key, Kind: in.Kind, TakenAt: bus.Timestamp(now), Managed: st.Managed, Truncated: st.Truncated,
		Images: st.Images, Volumes: st.Volumes, Networks: st.Networks, Unit: st.Unit, Containers: []container{}}
	s.StoppedServices = cut(append([]string{}, stop.Services...), maxServices, &s.Truncated)
	if !st.Managed {
		s.Note = noteOlder
		if in.Kind == "docker" {
			st.Containers = unlabelled(ctx, h, in.Name)
		}
	}
	for _, c := range st.Containers {
		s.Containers = append(s.Containers, container{WorkloadContainer: c, Metric: metrics.AppName(c.Name)})
	}
	s.fit()
	return s, nil
}

// unlabelled reads the state of a container an older agent started (it has no
// label), through the observer's read-only docker.inspect.
func unlabelled(ctx context.Context, h caller, name string) []helper.WorkloadContainer {
	var out helper.DockerOutput
	if err := h.Call(ctx, "docker.inspect", helper.DockerInspectRequest{Names: []string{name}}, &out); err != nil || !out.OK {
		return nil
	}
	line, _, _ := strings.Cut(strings.TrimSpace(out.Output), "\t")
	p := strings.Split(line, "|")
	if len(p) < 7 {
		return nil
	}
	c := helper.WorkloadContainer{Name: strings.TrimPrefix(p[0], "/"), State: p[1], Running: p[2] == "true", OOMKilled: p[4] == "true",
		StartedAt: p[6], Ports: []helper.WorkloadPort{}, Mounts: []helper.WorkloadMount{}, Networks: []string{}}
	c.ExitCode, _ = strconv.Atoi(p[3])
	c.Restarts, _ = strconv.Atoi(p[5])
	if len(p) > 7 {
		c.Health = p[7]
	}
	return []helper.WorkloadContainer{c}
}

func cut[T any](list []T, n int, truncated *bool) []T {
	if list == nil {
		return []T{}
	}
	if len(list) > n {
		*truncated = true
		return list[:n]
	}
	return list
}

func text(s string) string {
	if len(s) > maxText {
		return strings.ToValidUTF8(s[:maxText], "")
	}
	return s
}

// fit cuts the snapshot to the contract's limits and size.
func (s *Snapshot) fit() {
	t := &s.Truncated
	s.Containers = cut(s.Containers, helper.MaxContainers, t)
	for i := range s.Containers {
		c := &s.Containers[i]
		c.Image, c.Name, c.Service = text(c.Image), text(c.Name), text(c.Service)
		c.Ports = cut(c.Ports, maxPorts, t)
		c.Mounts = cut(c.Mounts, maxMounts, t)
		c.Networks = cut(c.Networks, maxNames, t)
		for j := range c.Mounts {
			m := &c.Mounts[j]
			m.Source, m.Destination, m.Name = text(m.Source), text(m.Destination), text(m.Name)
		}
	}
	s.Images = cut(s.Images, maxObjects, t)
	for i := range s.Images {
		s.Images[i].Tags = cut(s.Images[i].Tags, maxNames, t)
		s.Images[i].Digests = cut(s.Images[i].Digests, maxNames, t)
	}
	s.Volumes = cut(s.Volumes, maxObjects, t)
	s.Networks = cut(s.Networks, maxObjects, t)
	for i := range s.Networks {
		n := &s.Networks[i]
		n.Subnets, n.Gateways = cut(n.Subnets, maxSubnets, t), cut(n.Gateways, maxSubnets, t)
	}
	if s.size() <= maxSnapshot {
		return
	}
	s.Truncated = true
	for i := range s.Containers {
		s.Containers[i].Mounts = []helper.WorkloadMount{}
	}
	if s.size() <= maxSnapshot {
		return
	}
	s.Images = []helper.WorkloadImage{}
	for i := range s.Containers {
		s.Containers[i].Ports = []helper.WorkloadPort{}
	}
}

func (s *Snapshot) size() int {
	b, _ := json.Marshal(s)
	return len(b)
}

// fingerprint changes when something worth reporting changed: not the time,
// and not the memory and CPU counters that always move.
func (s *Snapshot) fingerprint() string {
	c := *s
	c.TakenAt = ""
	if s.Unit != nil {
		u := *s.Unit
		u.MemoryBytes, u.CPUSeconds = nil, nil
		c.Unit = &u
	}
	b, _ := json.Marshal(&c)
	return fmt.Sprintf("%x", sha256.Sum256(b))
}

// Refresh takes and sends the snapshot of one workload now (the
// workload_snapshot action, and after every other workload action).
func Refresh(ctx context.Context, d agent.Deps, h caller, in deploy.Installed) error {
	return refresh(ctx, d, h, in, diskEvery)
}

// RefreshAll is Refresh that also measures the disk space again.
func RefreshAll(ctx context.Context, d agent.Deps, h caller, in deploy.Installed) error {
	return refresh(ctx, d, h, in, diskRefresh)
}

func refresh(ctx context.Context, d agent.Deps, h caller, in deploy.Installed, diskAge time.Duration) error {
	now := time.Now()
	s, err := Take(ctx, h, in, workload.Stops{Store: d.State}.Get(in.Key), now)
	if err != nil {
		return err
	}
	s.measure(ctx, h, in, diskUse, diskAge, now)
	return send(ctx, d, s)
}

func send(ctx context.Context, d agent.Deps, s *Snapshot) error {
	token, err := d.Identity.Token()
	if err != nil {
		return err
	}
	err = d.API.SoftwareSnapshot(ctx, token, s)
	if api.IsAuth(err) {
		d.Identity.AuthFailed(token)
	}
	return err
}
