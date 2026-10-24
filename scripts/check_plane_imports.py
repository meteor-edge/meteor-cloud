#!/usr/bin/env python3
"""Fail if a plane imports another plane's private Python package.

Scans ``src/control-plane``, ``src/data-plane``, and ``src/device-plane``.
Does not scan ``tests/e2e``, ``tests/mqtt_live``, ``infrastructure/``, or
generated/cache dirs. Relative imports are ignored (they stay in-package).

Forbidden top-level packages:

- control-plane must not import ``data_plane``, ``edge_agent``, ``meteorcli``
- data-plane must not import ``app``, ``edge_agent``, ``meteorcli``
- device-plane (agent, gateway, runtime) must not import ``app``, ``data_plane``

Same-plane packages (``app``, ``data_plane``, ``edge_agent``, ``meteorcli``,
``runtime``) are allowed where they belong.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

# plane root relative to repo → forbidden top-level module names
PLANES: dict[str, frozenset[str]] = {
    "src/control-plane": frozenset({"data_plane", "edge_agent", "meteorcli"}),
    "src/data-plane": frozenset({"app", "edge_agent", "meteorcli"}),
    "src/device-plane": frozenset({"app", "data_plane"}),
}


def top_level_name(module: str | None) -> str | None:
    if not module or module.startswith("."):
        return None
    return module.split(".", 1)[0]


def imported_top_levels(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = top_level_name(alias.name)
                if top:
                    names.add(top)
        elif isinstance(node, ast.ImportFrom):
            top = top_level_name(node.module)
            if top:
                names.add(top)
    return names


def iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def check_tree(repo_root: Path) -> list[str]:
    violations: list[str] = []
    for rel, forbidden in PLANES.items():
        plane = repo_root / rel
        if not plane.is_dir():
            continue
        for path in iter_python_files(plane):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError) as exc:
                violations.append(f"{path}: cannot parse ({exc})")
                continue
            bad = imported_top_levels(tree) & forbidden
            if bad:
                display = path.relative_to(repo_root)
                violations.append(f"{display}: forbidden import {', '.join(sorted(bad))}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of scripts/)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else Path(__file__).resolve().parent.parent
    violations = check_tree(root)
    if violations:
        print("Cross-plane imports are not allowed:", file=sys.stderr)
        for line in violations:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"OK: no forbidden cross-plane imports under {root / 'src'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
