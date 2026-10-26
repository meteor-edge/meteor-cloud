"""Check registry and runner.

Each check returns evidence or raises: Fail (assertion), Blocked (a prerequisite
outside the check is missing) or NotSupported (the product has no such feature,
or this run did not configure it). A check whose dependency failed or was
blocked is BLOCKED. If every unmet dependency is not_supported, the check is
not_supported too (it was not in scope for this run).
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from e2e.redact import redact
from e2e.report import BLOCKED, FAIL, NOT_SUPPORTED, PASS, Report, Result


class Fail(AssertionError):
    pass


class Blocked(Exception):
    pass


class NotSupported(Exception):
    pass


@dataclass
class Check:
    id: str
    stage: str
    title: str
    func: Callable[[Any], dict[str, Any] | None]
    needs: tuple[str, ...] = ()
    when: Callable[[Any], bool] | None = None


REGISTRY: list[Check] = []


def check(id: str, stage: str, title: str, *, needs: tuple[str, ...] = (),
          when: Callable[[Any], bool] | None = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        REGISTRY.append(Check(id, stage, title, func, tuple(needs), when))
        return func

    return decorator


def expect(condition: Any, message: str) -> None:
    if not condition:
        raise Fail(message)


def wait_for(probe: Callable[[], Any], *, timeout: float, interval: float = 5, what: str = "condition") -> Any:
    """Poll until ``probe`` returns a truthy value; Fail with the last value otherwise."""
    deadline = time.monotonic() + timeout
    last: Any = None
    last_error = ""
    while True:
        try:
            last = probe()
            if last:
                return last
        except Fail:
            raise
        except Exception as exc:  # noqa: BLE001 - keep polling through transient errors
            last_error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise Fail(f"timed out after {timeout:.0f}s waiting for {what}"
                       + (f"; last error: {last_error}" if last_error else f"; last value: {last!r}"[:400]))
        time.sleep(interval)


@dataclass
class Runner:
    ctx: Any
    report: Report
    only: set[str] = field(default_factory=set)

    def run(self, checks: list[Check]) -> Report:
        for item in checks:
            if item.when is not None and not item.when(self.ctx):
                continue
            if self.only and item.stage not in self.only and item.id not in self.only:
                continue
            self.report.add(self._run_one(item))
        self.report.finished_at = time.time()
        return self.report

    def _run_one(self, item: Check) -> Result:
        missing = [n for n in item.needs if self.report.status_of(n) != PASS]
        if missing:
            reasons = ", ".join(f"{n}={self.report.status_of(n) or 'not run'}" for n in missing)
            # not_supported is a chosen skip (no domain, no API). Cascade it.
            # fail/blocked/not-run still block: something required did not succeed.
            status = (
                NOT_SUPPORTED
                if all(self.report.status_of(n) == NOT_SUPPORTED for n in missing)
                else BLOCKED
            )
            result = Result(item.id, item.stage, item.title, status, f"needs {reasons}")
            self._print(result)
            return result
        print(f"  ..       {item.id}  {item.title}", flush=True)
        started = time.monotonic()
        status, detail, evidence = PASS, "", {}
        try:
            evidence = item.func(self.ctx) or {}
        except Fail as exc:
            status, detail = FAIL, str(exc)
        except Blocked as exc:
            status, detail = BLOCKED, str(exc)
        except NotSupported as exc:
            status, detail = NOT_SUPPORTED, str(exc)
        except Exception as exc:  # noqa: BLE001 - an unexpected error is a failure with its traceback
            status = FAIL
            detail = f"{type(exc).__name__}: {exc}"
            evidence = {"traceback": traceback.format_exc()[-3000:]}
        result = Result(item.id, item.stage, item.title, status, redact(detail)[:2000],
                        _clean(evidence), round(time.monotonic() - started, 1))
        self._print(result)
        return result

    @staticmethod
    def _print(result: Result) -> None:
        mark = {PASS: "PASS", FAIL: "FAIL", BLOCKED: "BLOCKED", NOT_SUPPORTED: "N/SUPP"}[result.status]
        line = f"  {mark:<8} {result.id}  ({result.seconds:.0f}s)"
        if result.detail:
            line += f"\n           {result.detail[:500]}"
        print(redact(line), flush=True)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return redact(value)[:4000]
    return value
