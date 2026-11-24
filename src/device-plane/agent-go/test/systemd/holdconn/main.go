// Command holdconn is a test tool for test/systemd/run.sh: it keeps one
// connection to a Unix socket open without sending a request, reconnecting
// whenever the server drops it, until the given time is up. Four of them fill
// one user's connection slots on the agent socket.
package main

import (
	"fmt"
	"net"
	"os"
	"time"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: holdconn SOCKET SECONDS")
		os.Exit(2)
	}
	secs, err := time.ParseDuration(os.Args[2] + "s")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	end := time.Now().Add(secs)
	for time.Now().Before(end) {
		c, err := net.Dial("unix", os.Args[1])
		if err != nil {
			fmt.Println("dial:", err)
			time.Sleep(200 * time.Millisecond)
			continue
		}
		fmt.Println("connected")
		// Blocks until the server closes the connection (its read deadline).
		_ = c.SetReadDeadline(end)
		_, _ = c.Read(make([]byte, 1))
		c.Close()
	}
}
