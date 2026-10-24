"""Deterministic source archives of the working tree (committed and uncommitted files).

The same tree always gives the same bytes, so a repeated Ansible run sees no change.
"""

from __future__ import annotations

import gzip
import io
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


def build_archive(dest: Path, *, subdir: str = "") -> Path:
    """Write a .tar.gz of the repository (or ``subdir``, with paths relative to it)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in tracked_files(subdir):
            path = REPO_ROOT / name
            arcname = name[len(subdir.rstrip("/")) + 1 :] if subdir else name
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
