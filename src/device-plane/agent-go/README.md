# meteorcli (Go)

The Go rewrite of the MeteorCloud device agent. One binary: the always-on service
(`meteorcli agent`) and the command line. It speaks
[`contracts/device-agent-v1.md`](../../../contracts/device-agent-v1.md), the same contract as the
Python agent in [`../agent`](../agent/), which devices still install until this one is complete.

Design, file layout, import of a Python install and status: [docs/meteorcli-go.md](../../../docs/meteorcli-go.md).

```text
cmd/meteorcli/      main: version + cli.Main
internal/agent/     Module, Deps, supervisor, breaker, health, signals, systemd notify, logging, agent socket
internal/config/    paths, config.json, atomic writes, Python import
internal/state/     versioned state files, outbox
internal/api/       typed client for /api/v1/agent/*
internal/localrpc/  JSON request/answer over Unix sockets, SO_PEERCRED, audit lines
internal/helper/    root helper: uid check, operation allowlist (power, logs, systemd)
internal/cli/       one file per command, commands.go lists them
packaging/          systemd units, sysusers.d, tmpfiles.d
scripts/            build.sh (cross-build + size budget), check-coverage.sh
test/systemd/       units, sockets and permissions under real systemd (Docker)
```

```bash
go test -race ./...
./scripts/build.sh bin
METEORCLI_ROOT=/tmp/m go run ./cmd/meteorcli status
./test/systemd/run.sh   # needs Docker with --privileged
```

Rules for contributors (stdlib first, one job per package, owned goroutines, timeouts on every
outside call, atomic writes) are in the pull request that started this module and in
[docs/meteorcli-go.md](../../../docs/meteorcli-go.md).
