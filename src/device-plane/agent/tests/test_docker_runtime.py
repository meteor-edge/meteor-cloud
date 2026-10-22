"""Docker and Compose runtime adapter unit tests (mocked CLI)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from runtime.compose import ComposeAdapter, ComposeError, compose_available
from runtime.docker import DockerAdapter, DockerError, docker_available


def test_docker_apply_pull_runs_container(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args, *, check=True):
        del check
        calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    adapter = DockerAdapter(runner=runner)
    adapter.apply(
        container_name="cam",
        image="busybox:latest",
        ports=["8080:80"],
        env={"A": "1"},
        command='sh -c "echo hello"',
    )

    assert calls[0][:3] == ["docker", "rm", "-f"]
    run = calls[1]
    assert run[:5] == ["docker", "run", "-d", "--name", "cam"]
    assert "busybox:latest" in run
    assert "-p" in run and "8080:80" in run
    assert "-e" in run and "A=1" in run
    assert run[-3:] == ["sh", "-c", "echo hello"]


def test_docker_load_parses_image(tmp_path: Path) -> None:
    tarball = tmp_path / "img.tar"
    tarball.write_bytes(b"fake")
    seen: list[list[str]] = []

    def runner(args, *, check=True):
        del check
        seen.append(list(args))
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = "Loaded image: ghcr.io/ex/cam:1.2\n" if args[1] == "load" else ""
        return result

    DockerAdapter(runner=runner).apply(container_name="cam", tarball=tarball)
    run = next(c for c in seen if c[1] == "run")
    assert "ghcr.io/ex/cam:1.2" in run


def test_docker_apply_rejects_blank_name() -> None:
    with pytest.raises(DockerError):
        DockerAdapter(runner=MagicMock()).apply(container_name="  ", image="busybox")


def test_compose_apply_yaml(tmp_path: Path) -> None:
    artifact = tmp_path / "compose.yaml"
    artifact.write_text("services:\n  web:\n    image: nginx\n", encoding="utf-8")
    work = tmp_path / "work"
    seen: list[list[str]] = []

    def runner(args, *, check=True):
        del check
        seen.append(list(args))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    ComposeAdapter(runner=runner, work_root=work).apply(
        project_name="stack",
        artifact=artifact,
        env={"FOO": "bar"},
    )
    assert seen[0][0:3] == ["docker", "compose", "-p"]
    assert "up" in seen[0]
    assert (work / "stack" / ".env.meteorcloud").read_text(encoding="utf-8") == "FOO=bar\n"


def test_compose_apply_zip(tmp_path: Path) -> None:
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("docker-compose.yml", "services:\n  a:\n    image: alpine\n")
    work = tmp_path / "work"
    seen: list[list[str]] = []

    def runner(args, *, check=True):
        del check
        seen.append(list(args))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "cid\n"
        result.stderr = ""
        return result

    adapter = ComposeAdapter(runner=runner, work_root=work)
    adapter.apply(project_name="zipproj", artifact=zpath)
    assert (work / "zipproj" / "docker-compose.yml").is_file()
    assert adapter.health_check(project_name="zipproj") is True


def test_compose_zip_missing_file(tmp_path: Path) -> None:
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("readme.txt", "no compose")
    with pytest.raises(ComposeError, match="must contain"):
        ComposeAdapter(runner=MagicMock(), work_root=tmp_path / "w").apply(
            project_name="bad",
            artifact=zpath,
        )


def test_docker_available_false_without_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.docker.shutil.which", lambda _name: None)
    assert docker_available() is False


def test_compose_available_false_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.compose.shutil.which", lambda _name: None)
    assert compose_available() is False
