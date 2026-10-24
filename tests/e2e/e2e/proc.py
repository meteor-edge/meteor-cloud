"""Subprocess helper: streams redacted output to a log file."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from e2e.redact import redact


class CommandError(Exception):
    def __init__(self, args: Sequence[str], returncode: int, tail: str) -> None:
        super().__init__(f"{args[0]} exited with {returncode}")
        self.returncode = returncode
        self.tail = tail


def run_logged(
    args: Sequence[str],
    *,
    log: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    echo: bool = False,
    check: bool = True,
) -> tuple[int, str]:
    """Run a long command; its redacted output goes to ``log`` (and the terminal when ``echo``)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    started = time.monotonic()
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n$ {redact(' '.join(args))}\n")
        process = subprocess.Popen(
            list(args), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert process.stdout is not None
        for line in process.stdout:
            clean = redact(line)
            handle.write(clean)
            handle.flush()
            lines.append(clean)
            if echo:
                sys.stdout.write(clean)
        code = process.wait()
        handle.write(f"# exit={code} after {time.monotonic() - started:.0f}s\n")
    output = "".join(lines)
    if check and code != 0:
        raise CommandError(args, code, output[-4000:])
    return code, output
