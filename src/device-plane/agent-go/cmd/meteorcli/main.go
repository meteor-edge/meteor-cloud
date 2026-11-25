// Command meteorcli is the MeteorCloud device agent and its command line: one
// binary for the always-on service (`meteorcli agent`) and the short-lived
// commands people run (`meteorcli status`, `meteorcli config`, ...).
package main

import (
	"os"

	"meteorcloud/meteorcli/internal/cli"
)

// version is set at build time: -ldflags "-X main.version=1.2.3".
var version = "0.0.0-dev"

func main() {
	os.Exit(cli.Main(version, os.Args[1:], os.Stdout, os.Stderr))
}
