package helper

import (
	"regexp"
	"strconv"
	"strings"
)

// volumeRe is a named volume, its mount point and an optional ":ro". A name
// cannot contain "/", so a path on the device never matches.
var volumeRe = regexp.MustCompile(`^([a-zA-Z0-9][a-zA-Z0-9_.-]{0,191}):(/[^:\s]+)(:ro)?$`)

const (
	maxVolumes   = 20
	maxMemoryMB  = 4 << 20
	minMemoryMB  = 16 // Docker refuses less than 6 MB
	maxCPUMillis = 256_000
	minCPUMillis = 50
)

// validateOptions checks the volumes and limits of a docker.run.
func (r *DockerRunRequest) validateOptions() error {
	if len(r.Volumes) > maxVolumes {
		return badRequest("too many volumes")
	}
	for _, v := range r.Volumes {
		m := volumeRe.FindStringSubmatch(v)
		// The app's own volumes only: another app's data stays out of reach.
		if m == nil || !strings.HasPrefix(m[1], r.Name+"-") {
			return badRequest("%q is not a volume of %s", v, r.Name)
		}
		target := strings.TrimRight(m[2], "/")
		for _, part := range strings.Split(strings.TrimPrefix(target, "/"), "/") {
			if part == "" || part == "." || part == ".." {
				return badRequest("%q is not a mount point", m[2])
			}
		}
		if target == "/run" || strings.HasPrefix(target, "/run/secrets") {
			return badRequest("a volume cannot be mounted at %q", m[2])
		}
	}
	if r.MemoryMB != 0 && (r.MemoryMB < minMemoryMB || r.MemoryMB > maxMemoryMB) {
		return badRequest("memory_mb must be %d to %d", minMemoryMB, maxMemoryMB)
	}
	if r.CPUMillis != 0 && (r.CPUMillis < minCPUMillis || r.CPUMillis > maxCPUMillis) {
		return badRequest("cpu_millis must be %d to %d", minCPUMillis, maxCPUMillis)
	}
	return nil
}

// optionArgs are the docker run flags for the volumes and limits.
func (r *DockerRunRequest) optionArgs() []string {
	var args []string
	for _, v := range r.Volumes {
		args = append(args, "-v", v)
	}
	if r.MemoryMB > 0 {
		args = append(args, "--memory", strconv.Itoa(r.MemoryMB)+"m")
	}
	if r.CPUMillis > 0 {
		args = append(args, "--cpus", strconv.FormatFloat(float64(r.CPUMillis)/1000, 'f', -1, 64))
	}
	return args
}
