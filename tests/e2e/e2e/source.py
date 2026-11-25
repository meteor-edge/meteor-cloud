"""Deterministic source archives of the working tree (committed and uncommitted files).

The same tree always gives the same bytes, so a repeated Ansible run sees no change.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from e2e.config import REPO_ROOT

EXCLUDE_PREFIXES = ("tests/e2e/.state/", "tests/e2e/.env")


def tracked_files(subdir: str = "") -> list[str]:
    args = ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if subdir:
        args += ["--", subdir]
    out = subprocess.run(args, capture_output=True, check=True).stdout.decode()
    files = []
    for name in sorted(set(filter(None, out.split("\0")))):
        if name.startswith(EXCLUDE_PREFIXES) or "__pycache__/" in name:
            continue
        if (REPO_ROOT / name).is_file():
            files.append(name)
    return files


AGENT_GO = REPO_ROOT / "src" / "device-plane" / "agent-go"


# Test releases for the updater checks (stage 4b), shipped inside the device archive. The suite
# reads UPDATES_MANIFEST on the device to learn the versions.
UPDATES_DIR = "e2e-updates"
UPDATES_MANIFEST = f"{UPDATES_DIR}/versions.json"

# Passes the updater's `--version` smoke test, then fails to start: the updater must roll back.
BROKEN_AGENT = """#!/bin/sh
# meteorcloud e2e: a signed release that never comes up.
if [ "${1:-}" = "--version" ]; then echo "meteorcli @VERSION@"; exit 0; fi
echo "meteorcloud e2e: this build is broken on purpose" >&2
exit 1
"""


def release_test_key(key_file: Path) -> tuple[str, str]:
    """The environment's own release signing key pair (private, public), made once. Devices get
    a build that trusts only this key, so the suite can publish signed updates to them."""
    if not key_file.is_file():
        out = subprocess.run(["go", "run", "./tools/release", "genkey"], cwd=AGENT_GO, check=True,
                             capture_output=True, text=True).stdout
        pairs = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{pairs['private'].strip()}\n{pairs['public'].strip()}\n")
    private, public = key_file.read_text(encoding="utf-8").split()[:2]
    return private, public


def build_meteorcli(dest: Path, out_dir: Path, key_file: Path) -> Path:
    """Build the Go meteorcli release files (scripts/dist.sh) into ``out_dir`` and write them to
    ``dest`` as a .tar.gz. The version names the commit; the same build gives the same archive.

    The binaries trust only this environment's test key (``key_file``), and the archive also
    holds two signed test releases under UPDATES_DIR: a newer healthy build and a broken one."""
    commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    private, public = release_test_key(key_file)
    env = {**os.environ, "METEORCLI_RELEASE_KEYS": public, "METEORCLI_RELEASE_SIGNING_KEY": private}
    versions = {"commit": commit, "installed": f"0.0.0-e2e+{commit}",
                "update": f"0.0.0-e2e.2+{commit}", "broken": f"0.0.0-e2e.3+{commit}"}
    shutil.rmtree(out_dir, ignore_errors=True)

    def run(*args: str) -> None:
        subprocess.run(list(args), cwd=AGENT_GO, check=True, env=env, stdout=subprocess.DEVNULL)

    run("./scripts/dist.sh", str(out_dir), versions["installed"])

    updates = out_dir / UPDATES_DIR
    run("./scripts/build.sh", str(updates / "update"), versions["update"])
    run("go", "run", "./tools/release", "sign", str(updates / "update"), versions["update"])
    broken = updates / "broken"
    broken.mkdir(parents=True)
    for binary in sorted((updates / "update").glob("meteorcli-linux-*")):
        (broken / binary.name).write_text(BROKEN_AGENT.replace("@VERSION@", versions["broken"]))
        (broken / binary.name).chmod(0o755)
    run("go", "run", "./tools/release", "sign", str(broken), versions["broken"])
    (out_dir / UPDATES_MANIFEST).write_text(json.dumps(versions, indent=2) + "\n")

    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    return _write_archive(dest, [(p, str(p.relative_to(out_dir))) for p in files])


def build_archive(dest: Path, *, subdir: str = "") -> Path:
    """Write a .tar.gz of the repository (or ``subdir``, with paths relative to it)."""
    entries = []
    for name in tracked_files(subdir):
        arcname = name[len(subdir.rstrip("/")) + 1 :] if subdir else name
        entries.append((REPO_ROOT / name, arcname))
    return _write_archive(dest, entries)


def _write_archive(dest: Path, entries: list[tuple[Path, str]]) -> Path:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, arcname in entries:
            info = tarfile.TarInfo(arcname)
            data = path.read_bytes()
            info.size = len(data)
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with tmp.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(payload)
    if dest.is_file() and dest.read_bytes() == tmp.read_bytes():
        tmp.unlink()
    else:
        tmp.replace(dest)
    return dest
