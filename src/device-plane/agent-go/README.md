# meteorcli (Go)

The Go rewrite of the MeteorCloud device agent. One binary: the always-on service
(`meteorcli agent`) and the command line. It speaks
[`contracts/device-agent-v1.md`](../../../contracts/device-agent-v1.md), the same contract as the
Python agent in [`../agent`](../agent/), which devices still install until this one is complete.

Design, file layout, import of a Python install and status: [docs/meteorcli-go.md](../../../docs/meteorcli-go.md).

```text
cmd/meteorcli/      main: version + cli.Main
internal/agent/     Module, Deps, supervisor, breaker, health, signals, systemd notify, logging
internal/config/    paths, config.json, atomic writes, Python import
internal/state/     versioned state files, outbox
internal/api/       typed client for /api/v1/agent/*
internal/cli/       one file per command, commands.go lists them
scripts/            build.sh (cross-build + size budget), check-coverage.sh
```

```bash
go test -race ./...
./scripts/build.sh bin
METEORCLI_ROOT=/tmp/m go run ./cmd/meteorcli status
```

Rules for contributors (stdlib first, one job per package, owned goroutines, timeouts on every
outside call, atomic writes) are in the pull request that started this module and in
[docs/meteorcli-go.md](../../../docs/meteorcli-go.md).
