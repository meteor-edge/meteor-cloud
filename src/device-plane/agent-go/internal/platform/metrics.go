package platform

import (
	"context"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// cpuTimes returns the total and idle (idle + iowait) jiffies from /proc/stat.
// Only the first eight counters count: guest and guest_nice are already part
// of user and nice.
func (h *Host) cpuTimes() (total, idle uint64, ok bool) {
	for _, line := range strings.Split(h.readText("/proc/stat"), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 || fields[0] != "cpu" {
			continue
		}
		var values []uint64
		for _, f := range fields[1:] {
			if n, err := strconv.ParseUint(f, 10, 64); err == nil {
				values = append(values, n)
			}
		}
		if len(values) < 4 {
			return 0, 0, false
		}
		for _, v := range values[:min(len(values), 8)] {
			total += v
		}
		idle = values[3]
		if len(values) > 4 {
			idle += values[4]
		}
		return total, idle, true
	}
	return 0, 0, false
}

// CPUPercent samples /proc/stat twice, sample apart, and returns how busy the
// CPUs were in between (0-100). It returns false when ctx ends first.
func (h *Host) CPUPercent(ctx context.Context, sample time.Duration) (float64, bool) {
	t1, i1, ok := h.cpuTimes()
	if !ok {
		return 0, false
	}
	timer := time.NewTimer(sample)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return 0, false
	case <-timer.C:
	}
	t2, i2, ok := h.cpuTimes()
	// iowait may go down between samples; such a sample is skipped.
	if !ok || t2 <= t1 || i2 < i1 || i2-i1 > t2-t1 {
		return 0, false
	}
	busy := 1 - float64(i2-i1)/float64(t2-t1)
	return clampPercent(busy * 100), true
}

// Memory returns used and total bytes; used leaves out reclaimable cache (MemAvailable).
func (h *Host) Memory() (used, total uint64, ok bool) {
	totalKB, ok1 := h.meminfo("MemTotal")
	availKB, ok2 := h.meminfo("MemAvailable")
	if !ok1 || !ok2 || totalKB == 0 {
		return 0, 0, false
	}
	if availKB > totalKB {
		availKB = totalKB
	}
	return (totalKB - availKB) * 1024, totalKB * 1024, true
}

// Disk returns used and total bytes of the filesystem holding path. Used is
// total minus free blocks, as df counts it (the root reserve is not "used").
func (h *Host) Disk(path string) (used, total uint64, ok bool) {
	total, free, _, ok := h.Statfs(path)
	if !ok || total == 0 {
		return 0, 0, false
	}
	free = min(free, total)
	return total - free, total, true
}

// LoadAverage returns the 1, 5 and 15 minute load averages.
func (h *Host) LoadAverage() ([3]float64, bool) {
	var out [3]float64
	fields := strings.Fields(h.readText("/proc/loadavg"))
	if len(fields) < 3 {
		return out, false
	}
	for i := range out {
		v, err := strconv.ParseFloat(fields[i], 64)
		if err != nil {
			return out, false
		}
		out[i] = v
	}
	return out, true
}

// Uptime returns whole seconds since boot.
func (h *Host) Uptime() (int, bool) {
	fields := strings.Fields(h.readText("/proc/uptime"))
	if len(fields) == 0 {
		return 0, false
	}
	v, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return 0, false
	}
	return int(v), true
}

// TemperatureC returns the SoC temperature from the board's preferred thermal
// zones, falling back to thermal_zone0.
func (h *Host) TemperatureC() (float64, bool) {
	const root = "/sys/class/thermal"
	entries, _ := os.ReadDir(h.path(root))
	var names []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "thermal_zone") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	var candidates []string
	for _, want := range h.Board().ThermalZones {
		for _, name := range names {
			if strings.ToLower(h.readText(root+"/"+name+"/type")) == want {
				candidates = append(candidates, name)
			}
		}
	}
	candidates = append(candidates, "thermal_zone0")
	for _, name := range candidates {
		milli, err := strconv.Atoi(h.readText(root + "/" + name + "/temp"))
		if err == nil {
			return float64(milli) / 1000, true
		}
	}
	return 0, false
}

func clampPercent(v float64) float64 { return max(0, min(100, v)) }
