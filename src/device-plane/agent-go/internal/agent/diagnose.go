package agent

import (
	"context"
	"syscall"
	"time"

	"meteorcloud/meteorcli/internal/doctor"
)

// diagnoseTimeout bounds each outside call of a diagnosis (server, helper).
const diagnoseTimeout = 10 * time.Second

// HelperPinger is the part of the root helper client a diagnosis uses.
type HelperPinger interface {
	Call(ctx context.Context, op string, params, out any) error
}

// Diagnose gathers what the agent knows about itself and judges it. It backs
// `meteorcli doctor` (socket method "doctor") and the run_diagnostics action.
// It calls the server's health route and pings the root helper h (nil: not
// checked), each with a timeout; it never returns a secret.
func Diagnose(ctx context.Context, d Deps, h HelperPinger) doctor.Report {
	st := d.Identity.Status()
	f := doctor.Facts{
		Version: d.Version, Now: time.Now(), State: st.State, Server: st.Server,
		HeartbeatSeconds: st.HeartbeatSeconds, MQTTConfigured: st.MQTT,
		Modules: d.Health.Snapshot(), LastHeartbeat: d.Health.LastSuccess("heartbeat"),
	}
	if st.Server != "" && d.API != nil {
		cctx, cancel := context.WithTimeout(ctx, diagnoseTimeout)
		f.ServerChecked, f.ServerErr = true, d.API.Health(cctx)
		cancel()
	}
	if s := mqttState(d); s != nil {
		f.MQTT = &doctor.MQTTState{Connected: s.Connected, Broker: s.Broker, LastError: s.LastError}
	}
	if h != nil {
		cctx, cancel := context.WithTimeout(ctx, diagnoseTimeout)
		f.HelperChecked, f.HelperErr = true, h.Call(cctx, "ping", nil, nil)
		cancel()
	}
	f.DiskFree, f.DiskErr = freeBytes(d.Paths.StateDir)
	return doctor.Diagnose(f)
}

// freeBytes is the space an unprivileged process may still use under dir.
func freeBytes(dir string) (uint64, error) {
	var s syscall.Statfs_t
	if err := syscall.Statfs(dir, &s); err != nil {
		return 0, err
	}
	return s.Bavail * uint64(s.Bsize), nil
}
