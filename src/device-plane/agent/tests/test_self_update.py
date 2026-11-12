"""Safe meteorcli self-update: checksum, side-by-side install, rollback."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from meteorcli import cli as meteorcli

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "packaging" / "update-meteorcli.sh"
INSTALLCLI = ROOT / "installcli.sh"


def test_update_script_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(UPDATER)], check=True)
    text = UPDATER.read_text(encoding="utf-8")
    assert "refusing to update without SHA256SUMS" in text
    assert "pass --yes to continue" in text


def test_version_order_and_published_tag_selection(tmp_path: Path) -> None:
    payload = tmp_path / "releases.json"
    payload.write_text(
        """
        [
          {"tag_name": "cloud-v1.0.0", "draft": false, "prerelease": false},
          {"tag_name": "agent-v0.9.0-rc.1", "draft": false, "prerelease": true},
          {"tag_name": "agent-v0.3.9", "draft": false, "prerelease": false},
          {"tag_name": "agent-v0.3.10", "draft": false, "prerelease": false},
          {"tag_name": "agent-v0.4.0", "draft": true, "prerelease": false}
        ]
        """,
        encoding="utf-8",
    )

    def is_newer(left: str, right: str) -> bool:
        result = subprocess.run(
            ["bash", "-c", f'source "{UPDATER}"; version_is_newer "$1" "$2"', "bash", left, right],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    assert is_newer("0.3.4", "0.3.3")
    assert is_newer("0.3.10", "0.3.9")
    assert is_newer("0.10.0", "0.9.9")
    assert not is_newer("0.3.3", "0.3.3")
    assert not is_newer("0.3.3", "0.4.0")

    def is_valid(version: str) -> bool:
        result = subprocess.run(
            ["bash", "-c", f'source "{UPDATER}"; valid_version "$1"', "bash", version],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    assert is_valid("0.3.4")
    assert not is_valid("1.2")
    assert not is_valid("1.2.3-rc.1")
    assert not is_valid("")

    selected = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{UPDATER}"; latest_version_from_json "$1" < "$2"',
            "bash",
            "python3",
            str(payload),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.strip() == "0.3.10"


def test_fix_venv_paths_rewrites_shebangs_and_leaves_the_python_symlink(tmp_path: Path) -> None:
    root = tmp_path / "meteorcli.next"
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").symlink_to("/bin/sh")
    script = bindir / "meteorcli"
    script.write_text(f"#!{root}/bin/python\necho kept\n", encoding="utf-8")
    script.chmod(0o755)
    (root / "pyvenv.cfg").write_text(f"command = {root}\n", encoding="utf-8")
    (bindir / "activate").write_text(f'VIRTUAL_ENV="{root}"\n', encoding="utf-8")
    final = tmp_path / "meteorcli"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{UPDATER}"; fix_venv_paths "$1" "$1" "$2"',
            "bash",
            str(root),
            str(final),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    text = script.read_text(encoding="utf-8")
    assert text.startswith(f"#!{final}/bin/python\n")
    assert "echo kept\n" in text
    assert (bindir / "python").is_symlink()
    assert str(root) not in (root / "pyvenv.cfg").read_text(encoding="utf-8")
    assert str(final) in (bindir / "activate").read_text(encoding="utf-8")


def _release_fixture(tmp: Path, version: str, *, checksum: str | None = None) -> Path:
    fixture = tmp / "fixture"
    stage = tmp / "stage"
    agent = stage / f"meteorcli-{version}" / "agent"
    (agent / "meteorcli").mkdir(parents=True)
    (agent / "packaging").mkdir()
    (agent / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (agent / "meteorcli" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (agent / "installcli.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (agent / "packaging" / "update-meteorcli.sh").write_text("#!/bin/bash\necho updated\n", encoding="utf-8")
    fixture.mkdir()
    tarball = fixture / f"meteorcli-{version}.tar.gz"
    subprocess.run(
        ["tar", "-C", str(stage), "-czf", str(tarball), f"meteorcli-{version}"],
        check=True,
    )
    digest = subprocess.run(["sha256sum", tarball.name], cwd=fixture, check=True, capture_output=True, text=True)
    if checksum is not None:
        (fixture / "SHA256SUMS").write_text(f"{checksum}  {tarball.name}\n", encoding="utf-8")
    else:
        (fixture / "SHA256SUMS").write_text(digest.stdout, encoding="utf-8")
    (fixture / "releases.json").write_text(
        '[{"tag_name":"agent-v0.3.4","draft":false,"prerelease":false},'
        '{"tag_name":"not-meteorcli","draft":false,"prerelease":false}]',
        encoding="utf-8",
    )
    return fixture


def _install_fakes(fake_bin: Path) -> None:
    fake_bin.mkdir(exist_ok=True)
    python = fake_bin / "python"
    python.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            if [[ "${1:-}" == "-c" ]]; then
              exec /usr/bin/python3 "$@"
            fi
            if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
              dest="$3"
              mkdir -p "${dest}/bin"
              cat > "${dest}/bin/python" <<'PY'
            #!/bin/sh
            if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
              exit 0
            fi
            for arg in "$@"; do
              if [ "$arg" = "--version" ]; then
                echo "meteorcli ${METEORCLI_FAKE_VERSION}"
                exit 0
              fi
            done
            echo "fake-venv-python: $*" >&2
            exit 1
            PY
              chmod 0755 "${dest}/bin/python"
              printf '#!%s/bin/python\\n' "$dest" > "${dest}/bin/meteorcli"
              chmod 0755 "${dest}/bin/meteorcli"
              exit 0
            fi
            echo "fake-python: $*" >&2
            exit 1
            """
        ),
        encoding="utf-8",
    )
    python.chmod(0o755)
    for name in ("python3", "python3.11", "python3.12", "python3.13"):
        link = fake_bin / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(python)

    curl = fake_bin / "curl"
    curl.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            out=""
            url=""
            prev=""
            for arg in "$@"; do
              if [[ "$prev" == "-o" ]]; then
                out="$arg"
              fi
              case "$arg" in
                http://*|https://*) url="$arg" ;;
              esac
              prev="$arg"
            done
            if [[ -n "${CURL_LOG:-}" ]]; then
              printf '%s\\n' "$url" >> "$CURL_LOG"
            fi
            emit() {
              if [[ -n "$out" ]]; then
                cat "$1" > "$out"
              else
                cat "$1"
              fi
            }
            if [[ "$url" == *"/releases?"* ]]; then
              emit "${FIXTURE}/releases.json"
              exit 0
            fi
            base="$(basename "${url%%\\?*}")"
            if [[ ! -f "${FIXTURE}/${base}" ]]; then
              echo "curl fixture missing ${base} for ${url}" >&2
              exit 22
            fi
            emit "${FIXTURE}/${base}"
            """
        ),
        encoding="utf-8",
    )
    curl.chmod(0o755)

    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            printf '%s\\n' "$*" >> "${SYS_LOG}"
            cmd="$1"
            shift
            case "$cmd" in
              cat) exit 0 ;;
              is-active)
                [[ "$(cat "${SYS_STATE}")" == "active" ]]
                ;;
              stop)
                echo stopped > "${SYS_STATE}"
                ;;
              start|restart)
                if [[ "${SYS_FAIL_START:-}" == 1 ]]; then
                  echo failed > "${SYS_STATE}"
                else
                  echo active > "${SYS_STATE}"
                fi
                ;;
              show)
                prop=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    -p) prop="$2"; shift 2 ;;
                    --value) shift ;;
                    *) shift ;;
                  esac
                done
                case "$prop" in
                  MainPID) echo 1 ;;
                  NRestarts) echo 0 ;;
                  *) echo "" ;;
                esac
                ;;
              *) exit 0 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    systemctl.chmod(0o755)


def _current_install(prefix: Path, bindir: Path, version: str = "0.3.3") -> None:
    bindir.mkdir(parents=True)
    binary = prefix / "bin" / "meteorcli"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho 'meteorcli {version}'\n", encoding="utf-8")
    binary.chmod(0o755)
    link = bindir / "meteorcli"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(binary)


def _run_update(
    tmp: Path,
    fixture: Path,
    *args: str,
    fail_service: bool = False,
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp / "fake-bin"
    _install_fakes(fake_bin)
    prefix = tmp / "opt" / "meteorcli"
    bindir = tmp / "bin"
    config = tmp / "config"
    config.mkdir(exist_ok=True)
    token = config / "device-token"
    token.write_text("device-token-value\n", encoding="utf-8")
    token.chmod(0o600)
    (tmp / "home").mkdir(exist_ok=True)
    (tmp / "tmp").mkdir(exist_ok=True)
    state = tmp / "svc-state"
    state.write_text("active\n", encoding="utf-8")
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(tmp / "home"),
        "TMPDIR": str(tmp / "tmp"),
        "FIXTURE": str(fixture),
        "CURL_LOG": str(tmp / "curl.log"),
        "METEORCLI_FAKE_VERSION": extra.pop("METEORCLI_FAKE_VERSION", "0.3.4"),
        "METEORCLI_UPDATE_LOCK": str(tmp / "update.lock"),
        "METEORCLI_UPDATE_STATE_DIR": str(tmp / "state"),
        "METEORCLI_CONFIG_DIR": str(config),
        "METEORCLI_UPDATE_HEALTH_SECONDS": "0",
        "SYS_STATE": str(state),
        "SYS_LOG": str(tmp / "systemctl.log"),
        "SYS_FAIL_START": "1" if fail_service else "0",
    }
    env.update(extra)
    if not (prefix / "bin" / "meteorcli").exists():
        _current_install(prefix, bindir)
    return subprocess.run(
        ["bash", str(UPDATER), "--prefix", str(prefix), "--bin-dir", str(bindir), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _version(binary: Path, *, fake_version: str = "0.3.4") -> str:
    result = subprocess.run(
        [str(binary), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "METEORCLI_FAKE_VERSION": fake_version, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_check_reports_a_newer_release_without_downloading(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    result = _run_update(tmp_path, fixture, "--check")
    assert result.returncode == 10, result.stderr + result.stdout
    assert "update available: 0.3.3 -> 0.3.4" in result.stdout
    log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "releases?" in log
    assert ".tar.gz" not in log
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"
    assert (tmp_path / "config" / "device-token").read_text(encoding="utf-8") == "device-token-value\n"


def test_update_requires_yes_when_stdin_is_not_a_terminal(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    result = _run_update(tmp_path, fixture)
    assert result.returncode == 1, result.stdout
    assert "pass --yes" in result.stderr
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"
    assert not (tmp_path / "opt" / "meteorcli.next").exists()


def test_update_switches_after_checksum_and_keeps_the_previous_install(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    result = _run_update(tmp_path, fixture, "--yes")
    assert result.returncode == 0, result.stderr + result.stdout
    prefix = tmp_path / "opt" / "meteorcli"
    assert _version(prefix / "bin" / "meteorcli") == "meteorcli 0.3.4"
    assert _version(tmp_path / "opt" / "meteorcli.previous" / "bin" / "meteorcli") == "meteorcli 0.3.3"
    shebang = (prefix / "bin" / "meteorcli").read_text(encoding="utf-8").splitlines()[0]
    assert shebang == f"#!{prefix}/bin/python"
    assert ".next" not in shebang
    assert (tmp_path / "config" / "device-token").read_text(encoding="utf-8") == "device-token-value\n"
    status = (tmp_path / "state" / "status").read_text(encoding="utf-8")
    assert "current=0.3.4" in status
    assert "previous=0.3.3" in status
    installed = tmp_path / "home" / ".local" / "libexec" / "meteorcli-update"
    assert installed.is_file()
    assert "echo updated" in installed.read_text(encoding="utf-8")


def test_update_refuses_a_downgrade_before_downloading(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    result = _run_update(tmp_path, fixture, "--to", "0.3.2", "--yes")
    assert result.returncode == 1, result.stdout
    assert "refusing to replace 0.3.3 with older 0.3.2" in result.stderr
    log = (tmp_path / "curl.log").read_text(encoding="utf-8") if (tmp_path / "curl.log").exists() else ""
    assert ".tar.gz" not in log
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"


def test_update_refuses_a_bad_checksum_and_leaves_the_install(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4", checksum="0" * 64)
    result = _run_update(tmp_path, fixture, "--yes")
    assert result.returncode == 1, result.stdout
    assert "checksum mismatch" in result.stderr
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"
    assert not (tmp_path / "opt" / "meteorcli.next").exists()


def test_update_refuses_a_tarball_whose_version_does_not_match(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "9.9.9")
    tarball = fixture / "meteorcli-9.9.9.tar.gz"
    published = fixture / "meteorcli-0.3.4.tar.gz"
    tarball.rename(published)
    digest = subprocess.run(["sha256sum", published.name], cwd=fixture, check=True, capture_output=True, text=True)
    (fixture / "SHA256SUMS").write_text(digest.stdout, encoding="utf-8")
    result = _run_update(tmp_path, fixture, "--yes")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "does not match requested 0.3.4" in result.stderr
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"


def test_failed_service_health_restores_the_previous_install(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    result = _run_update(
        tmp_path,
        fixture,
        "--yes",
        fail_service=True,
        METEORCLI_UPDATE_ASSUME_SYSTEMD="1",
    )
    assert result.returncode == 1, result.stdout
    assert "did not stay active" in result.stderr
    assert "restoring the previous version" in result.stderr
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"
    assert (tmp_path / "config" / "device-token").read_text(encoding="utf-8") == "device-token-value\n"
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "stop meteorcli" in log
    assert "start meteorcli" in log


def test_rollback_restores_the_previous_binary(tmp_path: Path) -> None:
    fixture = _release_fixture(tmp_path, "0.3.4")
    updated = _run_update(tmp_path, fixture, "--yes")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    rolled = _run_update(tmp_path, fixture, "--rollback", "--yes")
    assert rolled.returncode == 0, rolled.stderr + rolled.stdout
    assert _version(tmp_path / "bin" / "meteorcli") == "meteorcli 0.3.3"
    assert (tmp_path / "config" / "device-token").read_text(encoding="utf-8") == "device-token-value\n"


def test_helpers_only_does_not_migrate_config_or_restart() -> None:
    subprocess.run(["bash", "-n", str(INSTALLCLI)], check=True)
    text = INSTALLCLI.read_text(encoding="utf-8")
    assert "--helpers-only" in text
    assert "meteorcli-update" in text
    start = text.index("refresh_unit_file() {")
    end = text.index("\n}", start)
    body = text[start:end]
    assert "migrate_user_config" not in body
    assert "systemctl restart" not in body
    assert "systemctl start" not in body
    if os.geteuid() == 0:
        return
    result = subprocess.run(
        ["bash", str(INSTALLCLI), "--helpers-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--helpers-only is only for a root install" in result.stderr


def test_update_command_forwards_flags_and_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "update.sh"
    captured = tmp_path / "captured"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{captured}'\nprintf '%s\\n' \"$METEORCLI_CONFIG_DIR\" >> '{captured}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("METEORCLI_UPDATE_SCRIPT", str(script))
    monkeypatch.setattr(meteorcli, "running_as_service", lambda: False)
    code = meteorcli.main(["--config-dir", str(tmp_path / "cfg"), "update", "--check", "--yes", "--to", "0.3.4"])
    assert code == 0
    text = captured.read_text(encoding="utf-8")
    assert "--check" in text
    assert "--yes" in text
    assert "--to 0.3.4" in text
    assert str(tmp_path / "cfg") in text


def test_update_command_refuses_to_run_as_the_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    script = tmp_path / "update.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("METEORCLI_UPDATE_SCRIPT", str(script))
    monkeypatch.setattr(meteorcli, "running_as_service", lambda: True)
    code = meteorcli.main(["update", "--yes"])
    assert code == 1
    assert "meteorcli.service" in capsys.readouterr().err


def test_update_command_explains_when_the_script_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("METEORCLI_UPDATE_SCRIPT", str(tmp_path / "missing"))
    monkeypatch.setattr(meteorcli, "running_as_service", lambda: False)
    code = meteorcli.main(["update", "--check"])
    assert code == 1
    assert "update-meteorcli.sh" in capsys.readouterr().err
