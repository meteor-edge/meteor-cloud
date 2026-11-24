#!/usr/bin/env python3
"""Fail when shipped third-party code carries a licence MeteorCloud must not ship by default.

MeteorCloud is installed for other companies (docs/licensing.md), so production
dependencies must stay permissive. This checks:

  --npm <package-lock.json>   console production packages (dev tooling is not shipped)
  --python <dist> [...]       installed Python distributions and everything they require
  --go-csv <file>             output of `go-licenses csv ./...` for the Go binaries

Copyleft or source-available licences (GPL, AGPL, SSPL, BSL/BUSL, Commons Clause,
Elastic) fail the check unless the package is in ALLOWED below, with a reason.
LGPL is allowed only through ALLOWED too (used unmodified, as a library).
Unknown licences fail, so a missing licence field is looked at by a person.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from importlib import metadata
from pathlib import Path

# package name (lowercase) -> why it is acceptable. Keep in step with docs/licensing.md.
ALLOWED: dict[str, str] = {
    "psycopg": "LGPL-3.0, used unmodified as a library by the control plane",
    "psycopg-binary": "LGPL-3.0 wheel of psycopg (bundles libpq, PostgreSQL licence)",
}

BLOCKED = re.compile(r"\b(A?GPL|LGPL|SSPL|BUSL|BSL|Commons[ -]Clause|Elastic[ -]License)", re.IGNORECASE)
# Short SPDX ids and the wording PyPI classifiers and metadata fields use.
PERMISSIVE = re.compile(
    r"\b(MIT|MIT-0|ISC|BSD|0BSD|Apache|Apache-2\.0|PSF|Python[- ]2\.0|Python Software Foundation|"
    r"Unlicense|CC0|BlueOak|Zlib|MPL|Mozilla Public|EPL|Eclipse Public|EDL|Eclipse Distribution|"
    r"HPND|Historical Permission|CC-BY-4\.0|PostgreSQL|Public Domain)\b",
    re.IGNORECASE,
)


def verdict(name: str, licence: str) -> str | None:
    """Return a problem description, or None when the package is fine."""
    key = name.lower()
    if key in ALLOWED:
        return None
    text = licence.strip()
    if not text or text.upper() in {"UNKNOWN", "?", "NONE"}:
        return f"{name}: no licence information"
    # "MIT OR GPL-2.0" is fine: we take the permissive option.
    options = [part for part in re.split(r"\s+OR\s+|\s*/\s*|\s*;\s*", text) if part]
    if any(PERMISSIVE.search(option) and not BLOCKED.search(option) for option in options):
        return None
    if BLOCKED.search(text):
        return f"{name}: {text}"
    return f"{name}: unrecognised licence {text!r}"


def check_npm(lockfile: Path) -> tuple[int, list[str]]:
    data = json.loads(lockfile.read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0
    for path, info in data.get("packages", {}).items():
        if not path or info.get("dev"):
            continue
        checked += 1
        name = path.rsplit("node_modules/", 1)[-1]
        licence = info.get("license")
        if isinstance(licence, dict):
            licence = licence.get("type", "")
        problem = verdict(name, str(licence or ""))
        if problem:
            problems.append(f"npm {problem}")
    return checked, problems


def _requirement_names(dist: metadata.Distribution) -> list[str]:
    names = []
    for requirement in dist.requires or []:
        if "extra ==" in requirement:
            continue
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if match:
            names.append(match.group(1))
    return names


def _python_licence(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c not in {"OSI Approved"}]
    if classifiers:
        return " OR ".join(classifiers)
    licence = (meta.get("License") or "").strip()
    # Some packages put the whole licence text here; the first line names it.
    return licence.splitlines()[0] if licence else ""


def check_python(roots: list[str]) -> tuple[int, list[str]]:
    seen: set[str] = set()
    problems: list[str] = []
    queue = list(roots)
    while queue:
        name = queue.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # optional dependency for another platform or Python version
        if name in roots:
            queue.extend(_requirement_names(dist))
            continue  # our own packages (Apache 2.0)
        problem = verdict(key, _python_licence(dist))
        if problem:
            problems.append(f"python {problem}")
        queue.extend(_requirement_names(dist))
    return len(seen) - len(roots), problems


def check_go_csv(path: Path) -> tuple[int, list[str]]:
    problems: list[str] = []
    checked = 0
    with path.open(encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if len(row) < 3:
                continue
            module, _url, licence = row[0], row[1], row[2]
            if module.startswith("meteorcloud/"):
                continue
            checked += 1
            problem = verdict(module, licence)
            if problem:
                problems.append(f"go {problem}")
    return checked, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--npm", type=Path, action="append", default=[])
    parser.add_argument("--python", nargs="+", default=[], metavar="DIST")
    parser.add_argument("--go-csv", type=Path, action="append", default=[])
    args = parser.parse_args(argv)

    problems: list[str] = []
    for lockfile in args.npm:
        count, found = check_npm(lockfile)
        print(f"npm {lockfile}: {count} production packages checked")
        problems += found
    if args.python:
        count, found = check_python(args.python)
        print(f"python {' '.join(args.python)}: {count} dependencies checked")
        problems += found
    for csv_path in args.go_csv:
        count, found = check_go_csv(csv_path)
        print(f"go {csv_path}: {count} modules checked")
        problems += found

    if problems:
        print("\nLicences that need a decision (see docs/licensing.md and AGENTS.md):", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("All checked licences are allowed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
