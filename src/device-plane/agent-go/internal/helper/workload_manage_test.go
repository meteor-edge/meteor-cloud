package helper

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"meteorcloud/meteorcli/internal/localrpc"
)

// manageHost answers docker by its arguments, so a test says which containers
// carry which labels.
func manageHost(t *testing.T) (Host, *[][]string, map[string]string) {
	t.Helper()
	f := &fakeHost{}
	h, root := deployHost(t, f)
	h.StateDir = filepath.Join(root, "state")
	answers := map[string]string{} // a substring of the command line -> stdout
	calls := &[][]string{}
	answer := func(name string, args []string) []byte {
		line := name + " " + strings.Join(args, " ")
		*calls = append(*calls, append([]string{name}, args...))
		best := ""
		for k := range answers {
			if strings.Contains(line, k) && len(k) > len(best) {
				best = k
			}
		}
		return []byte(answers[best])
	}
	h.Run = func(_ context.Context, name string, args ...string) ([]byte, error) { return answer(name, args), nil }
	h.Stream = func(_ context.Context, w io.Writer, name string, args ...string) error {
		_, err := w.Write(answer(name, args))
		return err
	}
	return h, calls, answers
}

func last(calls *[][]string) string { return strings.Join((*calls)[len(*calls)-1], " ") }

func control(kind, key, name, verb, service string) WorkloadControlRequest {
	return WorkloadControlRequest{WorkloadRequest: WorkloadRequest{Kind: kind, Key: key, Name: name}, Verb: verb, Service: service}
}

func TestWorkloadControlDocker(t *testing.T) {
	h, calls, answers := manageHost(t)
	// A container with the name but without our label: refused, not touched.
	_, err := call(t, h, "workload.control", control("docker", "shop", "shop", "stop", ""))
	if localrpc.CodeOf(err) != localrpc.CodeBadRequest || !strings.Contains(err.Error(), "deploy it again") {
		t.Fatalf("err = %v", err)
	}
	if len(*calls) != 1 || !strings.Contains(last(calls), "--filter label=meteorcloud.workload=shop") {
		t.Fatalf("ran %v", *calls)
	}
	answers["label=meteorcloud.workload=shop"] = "abc123\n"
	for _, verb := range []string{"restart", "stop", "start"} {
		if _, err := call(t, h, "workload.control", control("docker", "shop", "shop", verb, "")); err != nil {
			t.Fatal(err)
		}
		if got := last(calls); got != "docker "+verb+" abc123" {
			t.Errorf("ran %s", got)
		}
	}
}

func TestWorkloadControlCompose(t *testing.T) {
	h, calls, answers := manageHost(t)
	real, _ := filepath.EvalSymlinks(h.ComposeDir)
	ours := "label=com.docker.compose.project.working_dir=" + filepath.Join(real, "shop")
	project := "--no-trunc --filter label=com.docker.compose.project=shop"

	// The customer's own project of that name, from another directory.
	answers[project] = "foreign\n"
	answers[ours] = ""
	if _, err := call(t, h, "workload.control", control("docker_compose", "shop", "shop", "stop", "")); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Fatalf("foreign project: err = %v", err)
	}
	// Ours and foreign containers mixed.
	answers[ours] = "a1\n"
	answers[project] = "a1\nforeign\n"
	if _, err := call(t, h, "workload.control", control("docker_compose", "shop", "shop", "stop", "")); err == nil || !strings.Contains(err.Error(), "did not deploy") {
		t.Fatalf("mixed project: err = %v", err)
	}
	for _, c := range *calls {
		if c[1] == "compose" {
			t.Fatalf("compose ran for a refused project: %v", c)
		}
	}
	answers[ours] = "a1\na2\na3\n"
	answers[project] = "a1\na2\na3\n"
	answers["container inspect"] = `{"name":"/shop-web-1","service":"web","running":true}` + "\n" +
		`{"name":"/shop-db-1","service":"db","running":true}` + "\n" + `{"name":"/shop-migrate-1","service":"migrate","running":false}`
	for _, tt := range []struct{ verb, service, want string }{
		{"stop", "", "docker compose -p shop stop"},
		{"start", "", "docker compose -p shop start"},
		{"restart", "", "docker compose -p shop restart --no-deps db web"}, // not the finished job
		{"restart", "web", "docker compose -p shop restart --no-deps web"},
	} {
		if _, err := call(t, h, "workload.control", control("docker_compose", "shop", "shop", tt.verb, tt.service)); err != nil {
			t.Fatal(err)
		}
		if got := last(calls); got != tt.want {
			t.Errorf("ran %s, want %s", got, tt.want)
		}
	}
	// Only a finished job is left: a restart must not run it again.
	answers["container inspect"] = `{"name":"/shop-migrate-1","service":"migrate","running":false}`
	n := len(*calls)
	if _, err := call(t, h, "workload.control", control("docker_compose", "shop", "shop", "restart", "")); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("err = %v", err)
	}
	for _, c := range (*calls)[n:] {
		if c[1] == "compose" {
			t.Errorf("ran %v", c)
		}
	}
	for _, s := range []string{"nope", "--all"} {
		if _, err := call(t, h, "workload.control", control("docker_compose", "shop", "shop", "stop", s)); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("service %q: err = %v", s, err)
		}
	}
}

func TestWorkloadControlUnit(t *testing.T) {
	h, calls, _ := manageHost(t)
	_ = os.MkdirAll(h.UnitDir, 0o755)
	// A unit file that exists, but that the helper did not install.
	touch(t, filepath.Join(h.UnitDir, "mine.service"))
	if _, err := call(t, h, "workload.control", control("systemd", "mine.service", "mine.service", "stop", "")); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Fatalf("foreign unit: err = %v", err)
	}
	if _, err := call(t, h, "workload.control", control("systemd", "x", "meteorcli.service", "stop", "")); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Fatalf("meteorcli: err = %v", err)
	}
	if len(*calls) != 0 {
		t.Fatalf("ran %v", *calls)
	}
	if _, err := call(t, h, "systemd.install_unit", InstallUnitRequest{Name: "demo.service", Content: "[Service]\nExecStart=/bin/true\n"}); err != nil {
		t.Fatal(err)
	}
	for verb, want := range map[string]string{
		"restart": "systemctl restart -- demo.service", "stop": "systemctl disable --now -- demo.service", "start": "systemctl enable --now -- demo.service",
	} {
		if _, err := call(t, h, "workload.control", control("systemd", "demo.service", "demo.service", verb, "")); err != nil {
			t.Fatal(err)
		}
		if got := last(calls); got != want {
			t.Errorf("ran %s, want %s", got, want)
		}
	}
	if _, err := call(t, h, "systemd.control", SystemdControlRequest{Unit: "demo.service", Action: "remove"}); err != nil {
		t.Fatal(err)
	}
	if _, err := call(t, h, "workload.control", control("systemd", "demo.service", "demo.service", "start", "")); err == nil {
		t.Error("a removed unit can still be started")
	}
}

func TestWorkloadRequestsRejected(t *testing.T) {
	h, calls, _ := manageHost(t)
	for _, r := range []WorkloadControlRequest{
		control("docker", "a", "a", "kill", ""), control("docker", "a", "a", "rm", ""), control("docker", "", "a", "stop", ""),
		control("docker", "a=b c", "a", "stop", ""), control("docker", "a", "a", "stop", "web"), control("docker_compose", "a", "-x", "stop", ""),
		control("docker_compose", "a", "../a", "stop", ""), control("systemd", "a", "a.socket", "stop", ""), control("host", "a", "a", "stop", ""),
	} {
		if _, err := call(t, h, "workload.control", r); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
			t.Errorf("%+v: err = %v", r, err)
		}
	}
	if len(*calls) != 0 {
		t.Errorf("ran %v", *calls)
	}
}

func TestWorkloadInspect(t *testing.T) {
	h, calls, answers := manageHost(t)
	answers["ps -a -q"] = "abc\n"
	answers["container inspect"] = `{"id":"abc","name":"/shop","state":"running","running":true,"image_id":"sha256:1","restarts":2,` +
		`"port_map":{"80/tcp":[{"HostIp":"0.0.0.0","HostPort":"8080"}],"443/tcp":null},` +
		`"mounts":[{"type":"volume","name":"data","source":"/v","destination":"/d","rw":true}],"networks":["bridge",""]}`
	answers["image inspect"] = `{"id":"sha256:1","tags":["shop:1"],"digests":null,"size":5}`
	answers["volume inspect"] = `{"name":"data","driver":"local"}`
	answers["network inspect"] = `{"name":"bridge","driver":"bridge","scope":"local"}`
	out, err := call(t, h, "workload.inspect", WorkloadRequest{Kind: "docker", Key: "shop", Name: "shop"})
	if err != nil {
		t.Fatal(err)
	}
	var s WorkloadState
	_ = json.Unmarshal(out, &s)
	if !s.Managed || len(s.Containers) != 1 || len(s.Images) != 1 || len(s.Volumes) != 1 || len(s.Networks) != 1 {
		t.Fatalf("state %s", out)
	}
	c := s.Containers[0]
	if c.Name != "shop" || c.Restarts != 2 || len(c.Ports) != 2 || c.Ports[1].HostPort != "8080" || !slices.Equal(c.Networks, []string{"bridge"}) {
		t.Errorf("container %+v", c)
	}
	for _, c := range *calls {
		if line := strings.Join(c, " "); strings.Contains(line, ".Env") || strings.Contains(line, "Config.Cmd") {
			t.Errorf("reads secrets: %s", line)
		}
	}
	answers["systemctl show"] = "ActiveState=active\nSubState=running\nNRestarts=3\nMemoryCurrent=[not set]\nCPUUsageNSec=1500000000\n"
	out, err = call(t, h, "workload.inspect", WorkloadRequest{Kind: "systemd", Key: "a.service", Name: "a.service"})
	s = WorkloadState{}
	_ = json.Unmarshal(out, &s)
	if err != nil || s.Managed || s.Unit == nil || s.Unit.Restarts != 3 || s.Unit.MemoryBytes != nil || *s.Unit.CPUSeconds != 1.5 {
		t.Errorf("unit %s, %v", out, err)
	}
}

func TestDockerRunLabels(t *testing.T) {
	h, calls, _ := manageHost(t)
	if _, err := call(t, h, "docker.run", DockerRunRequest{Name: "a", Image: "nginx", Workload: "a", Release: "r-1"}); err != nil {
		t.Fatal(err)
	}
	if got := last(calls); !strings.Contains(got, "--label meteorcloud.workload=a --label meteorcloud.release=r-1 nginx") {
		t.Errorf("ran %s", got)
	}
	if _, err := call(t, h, "docker.run", DockerRunRequest{Name: "a", Image: "nginx", Workload: "a b"}); localrpc.CodeOf(err) != localrpc.CodeBadRequest {
		t.Errorf("err = %v", err)
	}
}

func TestWorkloadStatsAndDisk(t *testing.T) {
	h, calls, answers := manageHost(t)
	answers["ps -a -q"] = "a1\na2\n"
	answers["container inspect"] = `{"id":"a1","name":"/shop","running":true}` + "\n" + `{"id":"a2","name":"/old","running":false}`
	answers["stats --no-stream"] = `{"Name":"shop","CPUPerc":"1.50%","MemUsage":"2MiB / 1GiB","MemPerc":"0.2%","NetIO":"1.5kB / 2MB","BlockIO":"0B / 3GB","PIDs":"4"}`
	out, err := call(t, h, "workload.stats", WorkloadRequest{Kind: "docker", Key: "shop", Name: "shop"})
	var stats []WorkloadStats
	_ = json.Unmarshal(out, &stats)
	want := WorkloadStats{Name: "shop", CPUPercent: 1.5, MemoryBytes: 2 << 20, MemoryPercent: 0.2, NetRxBytes: 1500, NetTxBytes: 2e6, BlockWrite: 3e9, PIDs: 4}
	if err != nil || len(stats) != 1 || stats[0] != want {
		t.Errorf("stats %s, %v", out, err)
	}
	if got := last(calls); !strings.HasSuffix(got, " a1") {
		t.Errorf("ran %s", got)
	}
	answers["system df --format"] = `{"Type":"Images","TotalCount":"3","Active":"1","Size":"2GB","Reclaimable":"1.5GB (75%)"}`
	answers["system df -v"] = "data 5MB\nforeign 9GB\n"
	out, err = call(t, h, "docker.disk", DockerDiskRequest{Volumes: []string{"data"}})
	var d DockerDisk
	_ = json.Unmarshal(out, &d)
	if err != nil || len(d.Items) != 1 || d.Items[0] != (DiskUse{Type: "Images", Total: 3, Active: 1, Size: 2e9, Reclaimable: 15e8}) ||
		len(d.Volumes) != 1 || d.Volumes["data"] != 5e6 {
		t.Errorf("disk %s, %v", out, err)
	}
}
