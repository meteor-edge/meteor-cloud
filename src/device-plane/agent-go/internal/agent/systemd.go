package agent

import (
	"context"
	"fmt"
	"net"
	"os"
	"strconv"
	"time"
)

// notifyTimeout bounds one datagram to systemd; it never blocks the agent.
const notifyTimeout = time.Second

// Notifier speaks the sd_notify protocol without libsystemd: READY=1 once the
// agent is up, WATCHDOG=1 while it is healthy, STOPPING=1 on shutdown. A nil
// Notifier, or one without a socket, does nothing, so the agent also runs
// outside systemd.
type Notifier struct {
	socket   string
	watchdog time.Duration
}

// NotifierFromEnv reads NOTIFY_SOCKET, WATCHDOG_USEC and WATCHDOG_PID, which
// systemd sets for Type=notify units.
func NotifierFromEnv() *Notifier {
	n := &Notifier{socket: os.Getenv("NOTIFY_SOCKET")}
	usec, err := strconv.ParseInt(os.Getenv("WATCHDOG_USEC"), 10, 64)
	pid := os.Getenv("WATCHDOG_PID")
	if err == nil && usec > 0 && (pid == "" || pid == strconv.Itoa(os.Getpid())) {
		n.watchdog = time.Duration(usec) * time.Microsecond
	}
	return n
}

// WatchdogInterval is WatchdogSec from the unit, or 0 when there is no watchdog.
func (n *Notifier) WatchdogInterval() time.Duration {
	if n == nil || n.socket == "" {
		return 0
	}
	return n.watchdog
}

// Ready tells systemd the agent finished starting.
func (n *Notifier) Ready(ctx context.Context) error { return n.send(ctx, "READY=1") }

// Stopping tells systemd a clean shutdown began.
func (n *Notifier) Stopping(ctx context.Context) error { return n.send(ctx, "STOPPING=1") }

// Watchdog tells systemd the agent is alive.
func (n *Notifier) Watchdog(ctx context.Context) error { return n.send(ctx, "WATCHDOG=1") }

// Status sets the one-line status `systemctl status meteorcli` shows.
func (n *Notifier) Status(ctx context.Context, text string) error {
	return n.send(ctx, "STATUS="+text)
}

func (n *Notifier) send(ctx context.Context, msg string) error {
	if n == nil || n.socket == "" {
		return nil
	}
	addr := n.socket
	if addr[0] == '@' {
		addr = "\x00" + addr[1:] // abstract socket
	}
	ctx, cancel := context.WithTimeout(ctx, notifyTimeout)
	defer cancel()
	var d net.Dialer
	conn, err := d.DialContext(ctx, "unixgram", addr)
	if err != nil {
		return fmt.Errorf("sd_notify %s: %w", msg, err)
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetWriteDeadline(deadline)
	}
	if _, err := conn.Write([]byte(msg)); err != nil {
		return fmt.Errorf("sd_notify %s: %w", msg, err)
	}
	return nil
}
