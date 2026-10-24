"""Check results, the JSON report, and the terminal summary."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from e2e.redact import redact

PASS = "pass"
FAIL = "fail"
BLOCKED = "blocked"
NOT_SUPPORTED = "not_supported"
STATUSES = (PASS, FAIL, BLOCKED, NOT_SUPPORTED)
# Statuses that make the run fail. A skipped required check is never a success.
FAILING = (FAIL, BLOCKED)


@dataclass
class Result:
    id: str
    stage: str
    title: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")


@dataclass
class Report:
    env_name: str
    run_id: str
    meta: dict[str, Any] = field(default_factory=dict)
    results: list[Result] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def add(self, result: Result) -> None:
        self.results.append(result)

    def status_of(self, check_id: str) -> str | None:
        for result in self.results:
            if result.id == check_id:
                return result.status
        return None

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATUSES, 0)
        for result in self.results:
            counts[result.status] += 1
        return counts

    @property
    def ok(self) -> bool:
        return bool(self.results) and not any(r.status in FAILING for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_name": self.env_name,
            "run_id": self.run_id,
            "meta": self.meta,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "counts": self.counts(),
            "results": [asdict(r) for r in self.results],
        }

    def write(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "report.json"
        text_path = directory / "summary.txt"
        json_path.write_text(redact(json.dumps(self.to_dict(), indent=2, default=str)) + "\n", encoding="utf-8")
        text_path.write_text(self.summary() + "\n", encoding="utf-8")
        return json_path, text_path

    def summary(self) -> str:
        lines = [f"MeteorCloud E2E  env={self.env_name}  run={self.run_id}"]
        width = max((len(r.id) for r in self.results), default=10)
        stage = None
        for r in self.results:
            if r.stage != stage:
                stage = r.stage
                lines.append(f"\n[{stage}]")
            mark = {PASS: "PASS", FAIL: "FAIL", BLOCKED: "BLOCKED", NOT_SUPPORTED: "N/SUPP"}[r.status]
            line = f"  {mark:<8} {r.id:<{width}}  {r.title}"
            if r.detail and r.status != PASS:
                line += f"\n           {r.detail}"
            lines.append(line)
        counts = self.counts()
        lines.append("")
        lines.append("  ".join(f"{k}={v}" for k, v in counts.items()))
        lines.append("RESULT: " + ("PASS" if self.ok else "FAIL"))
        return redact("\n".join(lines))
