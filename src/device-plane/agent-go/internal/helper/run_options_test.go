package helper

import (
	"reflect"
	"testing"
)

func TestRunOptions(t *testing.T) {
	r := DockerRunRequest{Name: "web", Volumes: []string{"web-data:/data:ro"}, MemoryMB: 256, CPUMillis: 1500}
	if err := r.validateOptions(); err != nil {
		t.Fatal(err)
	}
	want := []string{"-v", "web-data:/data:ro", "--memory", "256m", "--cpus", "1.5"}
	if got := r.optionArgs(); !reflect.DeepEqual(got, want) {
		t.Errorf("args %v", got)
	}
	for _, v := range []string{
		"/etc:/host", "web-data", "other-data:/data", "web-data:data", "web-data:/data:rw", "web-data:/data:ro,z",
		"web-a/../b:/data", "web-data:/../etc", "web-data:/", "web-data:/run", "web-data:/run/secrets/x", "--privileged",
	} {
		bad := DockerRunRequest{Name: "web", Volumes: []string{v}}
		if bad.validateOptions() == nil {
			t.Errorf("%q was accepted", v)
		}
	}
	for _, bad := range []DockerRunRequest{{MemoryMB: 1}, {MemoryMB: -1}, {CPUMillis: 1}, {CPUMillis: 1 << 30}} {
		if bad.validateOptions() == nil {
			t.Errorf("%+v was accepted", bad)
		}
	}
}
