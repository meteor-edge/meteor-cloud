"""Docker and Compose runtime adapter unit tests (mocked CLI)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from runtime.compose import ComposeAdapter, ComposeError, compose_available
from runtime.docker import INSPECT_HEALTH_TEMPLATE, DockerAdapter, DockerError, docker_available


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
        if "ps" in args:
            result.stdout = '{"Name":"zipproj-a-1","Service":"a","State":"running","Health":"","ExitCode":0}\n'
        else:
            result.stdout = ""
        result.stderr = ""
        return result

    adapter = ComposeAdapter(runner=runner, work_root=work)
    adapter.apply(project_name="zipproj", artifact=zpath)
    assert (work / "zipproj" / "docker-compose.yml").is_file()
    assert adapter.inspect_health(project_name="zipproj").healthy


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


def _inspect_runner(states: dict[str, str | None], *, ps: str = "", calls: list | None = None):
    """Fake docker CLI: ``states`` maps container -> inspect output (None = missing)."""

    def runner(args, *, check=True):
        del check
        if calls is not None:
            calls.append(list(args))
        result = MagicMock()
        result.stderr = ""
        if args[1] == "inspect":
            state = states.get(args[-1])
            result.returncode = 1 if state is None else 0
            result.stdout = state or ""
        else:  # docker compose -p NAME ps -a --format json
            result.returncode = 0
            result.stdout = ps
        return result

    return runner


def _ps(*services: dict) -> str:
    """``docker compose ps --format json`` output, one object per line (Compose >= 2.21)."""
    return "\n".join(json.dumps(service) for service in services) + "\n"


@pytest.mark.parametrize(
    ("inspect", "status", "problem"),
    [
        ("true|running|0|", "healthy", None),
        ("true|running|0|healthy", "healthy", None),
        ("true|running|0|unhealthy", "unhealthy", "container web HEALTHCHECK failed"),
        ("true|running|0|starting", "unknown", "container web HEALTHCHECK starting"),
        ("false|exited|1|", "unhealthy", "container web is not running (exited, exit 1)"),
        (None, "missing", "container web not found"),
    ],
)
def test_docker_inspect_health(inspect: str | None, status: str, problem: str | None) -> None:
    calls: list[list[str]] = []
    adapter = DockerAdapter(runner=_inspect_runner({"web": inspect}, calls=calls))
    health = adapter.inspect_health(container_name="web")
    assert (health.status, health.problem) == (status, problem)
    assert calls == [["docker", "inspect", "-f", INSPECT_HEALTH_TEMPLATE, "web"]]


def test_inspect_template_reads_state_only() -> None:
    assert "Env" not in INSPECT_HEALTH_TEMPLATE
    assert "Config" not in INSPECT_HEALTH_TEMPLATE
    assert "Label" not in INSPECT_HEALTH_TEMPLATE
    assert "Log" not in INSPECT_HEALTH_TEMPLATE


@pytest.mark.parametrize("name", ["", "  ", "a/b", ".hidden"])
def test_docker_health_validates_name(name: str) -> None:
    with pytest.raises(DockerError):
        DockerAdapter(runner=_inspect_runner({})).inspect_health(container_name=name)


def test_compose_all_services_running_is_healthy_in_one_call(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    runner = _inspect_runner(
        {},
        ps=_ps(
            {"Service": "web", "State": "running", "Health": "healthy", "ExitCode": 0},
            {"Service": "db", "State": "running", "Health": "", "ExitCode": 0},
        ),
        calls=calls,
    )
    health = ComposeAdapter(runner=runner, work_root=tmp_path).inspect_health(project_name="shop")
    assert health.status == "healthy"
    assert calls == [["docker", "compose", "-p", "shop", "ps", "-a", "--format", "json"]]


def test_compose_ps_json_array_from_older_compose(tmp_path: Path) -> None:
    ps = json.dumps([{"Service": "web", "State": "running", "Health": "", "ExitCode": 0}])
    health = ComposeAdapter(runner=_inspect_runner({}, ps=ps), work_root=tmp_path).inspect_health(project_name="shop")
    assert health.status == "healthy"


def test_compose_one_service_down_is_unhealthy(tmp_path: Path) -> None:
    runner = _inspect_runner(
        {},
        ps=_ps(
            {"Service": "sidecar", "State": "running", "Health": "", "ExitCode": 0},
            {"Service": "web", "State": "exited", "Health": "", "ExitCode": 137},
        ),
    )
    health = ComposeAdapter(runner=runner, work_root=tmp_path).inspect_health(project_name="shop")
    assert health.status == "unhealthy"
    assert health.problem == "compose service web is not running (exited, exit 137)"


def test_compose_finished_one_shot_service_is_ignored(tmp_path: Path) -> None:
    ps = _ps(
        {"Service": "web", "State": "running", "Health": "", "ExitCode": 0},
        {"Service": "migrate", "State": "exited", "Health": "", "ExitCode": 0},
    )
    adapter = ComposeAdapter(runner=_inspect_runner({}, ps=ps), work_root=tmp_path)
    assert adapter.inspect_health(project_name="shop").status == "healthy"
    only_done = _ps({"Service": "migrate", "State": "exited", "Health": "", "ExitCode": 0})
    health = ComposeAdapter(runner=_inspect_runner({}, ps=only_done), work_root=tmp_path).inspect_health(
        project_name="shop"
    )
    assert (health.status, health.problem) == ("unhealthy", "compose project shop has no running services")


def test_compose_healthcheck_failure_is_unhealthy(tmp_path: Path) -> None:
    runner = _inspect_runner({}, ps=_ps({"Service": "web", "State": "running", "Health": "unhealthy"}))
    health = ComposeAdapter(runner=runner, work_root=tmp_path).inspect_health(project_name="shop")
    assert health.problem == "compose service web HEALTHCHECK failed"


def test_compose_without_services_is_missing(tmp_path: Path) -> None:
    adapter = ComposeAdapter(runner=_inspect_runner({}, ps=""), work_root=tmp_path)
    health = adapter.inspect_health(project_name="shop")
    assert health.status == "missing"
    assert health.problem == "compose project shop has no services"


def test_compose_health_validates_project_name(tmp_path: Path) -> None:
    with pytest.raises(ComposeError):
        ComposeAdapter(runner=_inspect_runner({}), work_root=tmp_path).inspect_health(project_name="../x")
