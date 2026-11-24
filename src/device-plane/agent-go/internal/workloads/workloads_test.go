package workloads

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"meteorcloud/meteorcli/internal/agenttest"
	"meteorcloud/meteorcli/internal/deploy"
	"meteorcloud/meteorcli/internal/fakecp"
	"meteorcloud/meteorcli/internal/helper"
	"meteorcloud/meteorcli/internal/workload"
)

const route = "/api/v1/agent/software/snapshots"

type fakeHelper struct {
	state   helper.WorkloadState
	inspect string
	stats   []helper.WorkloadStats
	disk    helper.DockerDisk
	disks   int
}

func (f *fakeHelper) Call(_ context.Context, op string, _, out any) error {
	var v any = f.state
	if op == "docker.inspect" {
		v = helper.DockerOutput{OK: true, Output: f.inspect}
	}
	switch op {
	case "workload.stats":
		v = f.stats
	case "docker.disk":
		f.disks++
		v = f.disk
	}
	b, _ := json.Marshal(v)
	return json.Unmarshal(b, out)
}

func TestLookSendsOnChangeAndPeriodically(t *testing.T) {
	cp := fakecp.New(t)
	cp.On("POST", route, fakecp.OK(map[string]any{"accepted": true}))
	d := agenttest.Deps(t, cp, nil, nil)
	item := map[string]any{"workload_key": "a.service", "kind": "systemd", "release_id": "r", "revision": 1, "spec": map[string]any{}}
	if err := d.State.Write("applied-software.json", 1, struct {
		W map[string]any `json:"workloads"`
	}{map[string]any{"a.service": map[string]any{"release_id": "r", "revision": 1, "item": item}}}); err != nil {
		t.Fatal(err)
	}
	mem := int64(1)
	h := &fakeHelper{state: helper.WorkloadState{Managed: true, Unit: &helper.WorkloadUnit{ActiveState: "active", MemoryBytes: &mem}}}
	now := time.Unix(1_700_000_000, 0)
	l := &looker{d: d, helper: h, now: func() time.Time { return now }, sent: map[string]sent{}}
	want := func(n int, what string) {
		t.Helper()
		l.look(context.Background())
		if got := len(cp.Calls("POST", route)); got != n {
			t.Fatalf("%s: %d snapshots, want %d", what, got, n)
		}
	}
	want(1, "first look")
	mem = 2
	now = now.Add(time.Minute)
	want(1, "only memory moved")
	h.state.Unit.ActiveState = "failed"
	want(2, "state changed")
	now = now.Add(resendEvery)
	want(3, "periodic")
	// An older server without the route: quiet for a while.
	cp.On("POST", route, fakecp.Fail(404, "not_found"))
	now = now.Add(resendEvery)
	want(4, "404")
	now = now.Add(resendEvery)
	want(4, "quiet")
}

func TestUnlabelledContainerIsReadOnly(t *testing.T) {
	h := &fakeHelper{inspect: "/web|running|true|0|false|2|2026-01-01T00:00:00Z|healthy|1,2;\t\t\t\n"}
	s, err := Take(context.Background(), h, deploy.Installed{Key: "web", Kind: "docker", Name: "web"}, workload.Stop{}, time.Now())
	if err != nil || s.Managed || s.Note == "" || len(s.Containers) != 1 || s.Containers[0].Health != "healthy" || s.Containers[0].Metric != "web" {
		t.Errorf("%+v, %v", s, err)
	}
}

func TestSnapshotFits(t *testing.T) {
	h := &fakeHelper{state: helper.WorkloadState{Managed: true}}
	long := strings.Repeat("x", 2000)
	for range 20 {
		c := helper.WorkloadContainer{Name: "c"}
		for range 40 {
			c.Networks = append(c.Networks, long[:128])
		}
		for range 100 {
			c.Mounts = append(c.Mounts, helper.WorkloadMount{Source: long, Destination: long})
		}
		h.state.Containers = append(h.state.Containers, c)
	}
	s, err := Take(context.Background(), h, deploy.Installed{Key: "k", Kind: "docker_compose", Name: "k"}, workload.Stop{}, time.Now())
	if err != nil || s.size() > maxSnapshot || !s.Truncated || len(s.Containers) != 20 {
		t.Errorf("size %d truncated %v err %v", s.size(), s.Truncated, err)
	}
}

func TestMeasure(t *testing.T) {
	h := &fakeHelper{
		state: helper.WorkloadState{Managed: true, Containers: []helper.WorkloadContainer{{Name: "web", ID: "w"}, {Name: "db", ID: "d"}},
			Volumes: []helper.WorkloadVolume{{Name: "data"}}},
		stats: []helper.WorkloadStats{{Name: "web", CPUPercent: 2.5, MemoryBytes: 10}},
		disk:  helper.DockerDisk{Items: []helper.DiskUse{{Type: "Images", Reclaimable: 7}}, Volumes: map[string]int64{"data": 42}, Containers: map[string]int64{"w": 9}},
	}
	in := deploy.Installed{Key: "shop", Kind: "docker_compose", Name: "shop"}
	now := time.Unix(1_700_000_000, 0)
	c := &disks{}
	take := func(age time.Duration) *Snapshot {
		s, err := Take(context.Background(), h, in, workload.Stop{}, now)
		if err != nil {
			t.Fatal(err)
		}
		before := s.fingerprint()
		s.measure(context.Background(), h, in, c, age, now)
		if before == s.fingerprint() {
			t.Error("nothing was measured")
		}
		return s
	}
	s := take(diskEvery)
	if s.Containers[0].Stats == nil || s.Containers[0].Stats.CPUPercent != 2.5 || s.Containers[1].Stats != nil ||
		s.Containers[0].SizeRW == nil || *s.Containers[0].SizeRW != 9 || s.Containers[1].SizeRW != nil ||
		s.Disk.Items[0].Reclaimable != 7 || *s.Volumes[0].Size != 42 {
		t.Errorf("%+v", s)
	}
	now = now.Add(10 * time.Minute)
	if s = take(diskEvery); h.disks != 1 || s.Disk == nil {
		t.Errorf("measured %d times within half an hour", h.disks)
	}
	if take(diskRefresh); h.disks != 2 {
		t.Error("a refresh did not measure again")
	}
}

func TestNewVolumeIsMeasuredSoon(t *testing.T) {
	h := &fakeHelper{disk: helper.DockerDisk{Items: []helper.DiskUse{{Type: "Images"}}}}
	c, now := &disks{}, time.Unix(1_700_000_000, 0)
	get := func(want int, volumes ...helper.WorkloadVolume) {
		t.Helper()
		if c.get(context.Background(), h, &Snapshot{WorkloadKey: "a", Volumes: volumes}, diskEvery, now); h.disks != want {
			t.Errorf("measured %d times, want %d", h.disks, want)
		}
	}
	get(1)
	get(1, helper.WorkloadVolume{Name: "data"}) // seen right after a measurement
	now = now.Add(5 * time.Minute)
	get(2, helper.WorkloadVolume{Name: "data"})
	now = now.Add(5 * time.Minute)
	get(2, helper.WorkloadVolume{Name: "data"})
}
