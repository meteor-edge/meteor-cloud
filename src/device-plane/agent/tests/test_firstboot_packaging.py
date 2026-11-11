"""Sanity-check first-boot packaging files shipped for golden images."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

FIRSTBOOT = Path(__file__).resolve().parents[1] / "packaging" / "firstboot"


def test_firstboot_script_is_valid_bash() -> None:
    for name in ("meteorcli-firstboot.sh", "install-meteorcli-firstboot.sh"):
        script = FIRSTBOOT / name
        assert script.is_file()
        subprocess.run(["bash", "-n", str(script)], check=True)
    subprocess.run(["bash", "-n", str(FIRSTBOOT / "firstrun.sh")], check=True)
    firstboot = (FIRSTBOOT / "meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "wireguard-tools" in firstboot


def test_installcli_installs_wireguard_tools() -> None:
    installcli = Path(__file__).resolve().parents[1] / "installcli.sh"
    text = installcli.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(installcli)], check=True)
    assert "install_host_packages" in text
    assert "wireguard-tools" in text


INSTALLCLI = Path(__file__).resolve().parents[1] / "installcli.sh"


def _installcli_function(name: str) -> str:
    """One shell function from installcli.sh, so it can run without the install itself."""
    lines = INSTALLCLI.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1])


def _run_installcli_function(name: str, script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"{_installcli_function(name)}\n{script}"],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def test_installcli_starts_the_service_by_default() -> None:
    text = INSTALLCLI.read_text(encoding="utf-8")
    assert "WITH_SERVICE=auto" in text
    assert "--no-service)" in text
    assert "systemctl restart meteorcli.service" in text
    # Packages and helpers before the service: it may bring up WireGuard at once.
    body = _installcli_function("install_meteorcli")
    assert body.index("install_root_helpers") < body.index("want_service")


def test_want_service_honours_explicit_flags() -> None:
    assert _run_installcli_function("want_service", "WITH_SERVICE=1; want_service").returncode == 0
    assert _run_installcli_function("want_service", "WITH_SERVICE=0; want_service").returncode == 1


def test_want_service_auto_skips_for_a_user_install() -> None:
    if os.geteuid() == 0:
        return
    result = _run_installcli_function("want_service", "WITH_SERVICE=auto; want_service")
    assert result.returncode == 1
    assert "no background service for a per-user install" in result.stderr


def test_need_writable_accepts_a_missing_home_local(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ok = _run_installcli_function("need_writable", f'need_writable "{home}/.local/share/meteorcli"')
    assert ok.returncode == 0
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)
    if os.geteuid() != 0:
        denied = _run_installcli_function("need_writable", f'need_writable "{locked}/a/b"')
        assert denied.returncode == 1


def test_firstboot_installer_points_at_repo() -> None:
    installer = (FIRSTBOOT / "install-meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "https://github.com/meteor-edge/meteor-cloud" in installer
    assert "install-meteorcli-firstboot.sh" in installer
    assert "[1/4] Fetching first-boot files" in installer
    assert "domain:   MISSING" in installer
    assert "journalctl -u meteorcli-firstboot -n 40" in installer
    cloud = (FIRSTBOOT / "cloud-init-user-data.yaml").read_text(encoding="utf-8")
    assert cloud.lstrip().startswith("#cloud-config")
    assert "install-meteorcli-firstboot.sh" in cloud
    assert "runcmd:" in cloud


def test_firstboot_unit_polls_until_token() -> None:
    unit = (FIRSTBOOT / "meteorcli-firstboot.service").read_text(encoding="utf-8")
    assert "Type=simple" in unit
    assert "ExecStart=/usr/local/sbin/meteorcli-firstboot.sh --poll" in unit
    assert "ConditionPathExists=!/etc/meteorcli/device-token" in unit
    assert "Environment=HOME=/root" in unit
    script = (FIRSTBOOT / "meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "meteorcli request-token" in script
    assert "meteorcli claim --wait" in script
    assert "--wait 0" in script
    assert "handoff_poll_to_background" in script
    assert "device-token" in script
    assert "inherit_from_sudo_caller" in script
    assert "load_dot_meteorenv" in script
    assert "load_boot_meteorenv" in script
    assert "remove_firstboot" in script
    assert "/boot/firmware" in script
    assert ".meteorenv" in script


def _meteorenv_loader_snippet() -> str:
    src = (FIRSTBOOT / "meteorcli-firstboot.sh").read_text(encoding="utf-8")
    start = src.index("# Fill METEOR_* only when empty")
    end = src.index("# sudo env_reset drops")
    return src[start:end]


def test_meteorenv_file_fills_empty_vars(tmp_path: Path) -> None:
    env_path = tmp_path / ".meteorenv"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "export METEOR_DOMAIN=192.168.0.118:8000",
                'METEOR_API_KEY="key_from_file"',
                "METEOR_NAME=pi-lab",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    driver = f"""
set -euo pipefail
log() {{ echo "$@"; }}
{_meteorenv_loader_snippet()}
load_meteor_kv_file "{env_path}"
printf '%s\\n' "${{METEOR_DOMAIN}}" "${{METEOR_API_KEY}}" "${{METEOR_NAME}}"
"""
    result = subprocess.run(["bash", "-c", driver], check=True, capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if not line.startswith("Loaded ")]
    assert lines == ["192.168.0.118:8000", "key_from_file", "pi-lab"]


def test_meteorenv_file_does_not_override_environment(tmp_path: Path) -> None:
    env_path = tmp_path / ".meteorenv"
    env_path.write_text(
        "METEOR_DOMAIN=from-file.example:8000\nMETEOR_API_KEY=key_from_file\n",
        encoding="utf-8",
    )
    driver = f"""
set -euo pipefail
log() {{ echo "$@"; }}
METEOR_DOMAIN=from-env.example:8000
METEOR_API_KEY=key_from_env
{_meteorenv_loader_snippet()}
load_meteor_kv_file "{env_path}"
printf '%s\\n' "${{METEOR_DOMAIN}}" "${{METEOR_API_KEY}}"
"""
    result = subprocess.run(["bash", "-c", driver], check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ["from-env.example:8000", "key_from_env"]


def test_boot_meteorenv_is_loaded_from_boot_dir(tmp_path: Path) -> None:
    boot = tmp_path / "firmware"
    boot.mkdir()
    (boot / ".meteorenv").write_text(
        "METEOR_DOMAIN=boot.example:8000\nMETEOR_API_KEY=key_from_boot\n",
        encoding="utf-8",
    )
    driver = f"""
set -euo pipefail
log() {{ echo "$@"; }}
BOOT_MOUNT_CLEANUP=""
{_meteorenv_loader_snippet()}
METEOR_BOOT_DIR="{boot}"
load_boot_meteorenv
printf '%s\\n' "${{METEOR_DOMAIN}}" "${{METEOR_API_KEY}}"
"""
    result = subprocess.run(["bash", "-c", driver], check=True, capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if not line.startswith("Loaded ")]
    assert lines == ["boot.example:8000", "key_from_boot"]


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_empty_image_boot_meteorenv_installs_agent_and_removes_firstboot(tmp_path: Path) -> None:
    """Empty image: .meteorenv on boot, firstboot downloads/runs, then removes itself."""
    boot = tmp_path / "boot" / "firmware"
    boot.mkdir(parents=True)
    (boot / ".meteorenv").write_text(
        "METEOR_DOMAIN=192.168.0.118:8000\nMETEOR_API_KEY=key_from_boot\nMETEOR_NAME=pi-lab\n",
        encoding="utf-8",
    )

    config_dir = tmp_path / "etc" / "meteorcli"
    env_file = tmp_path / "etc" / "meteorcli-firstboot.env"
    unit_path = tmp_path / "etc" / "systemd" / "system" / "meteorcli-firstboot.service"
    installed = tmp_path / "usr" / "local" / "sbin" / "meteorcli-firstboot.sh"
    stub_bin = tmp_path / "stub-bin"
    systemctl_log = tmp_path / "systemctl.log"
    calls_log = config_dir / "meteorcli-calls.log"

    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "METEOR_DOMAIN=cloud.example.com\nMETEOR_API_KEY=key_replace_me\n",
        encoding="utf-8",
    )
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIRSTBOOT / "meteorcli-firstboot.service", unit_path)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIRSTBOOT / "meteorcli-firstboot.sh", installed)
    installed.chmod(0o755)

    _write_executable(
        stub_bin / "curl",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        stub_bin / "systemctl",
        f"""#!/usr/bin/env bash
echo "$*" >> "{systemctl_log}"
exit 0
""",
    )
    _write_executable(
        stub_bin / "meteorcli",
        f"""#!/usr/bin/env bash
set -euo pipefail
CONFIG="${{METEORCLI_CONFIG_DIR:-{config_dir}}}"
mkdir -p "$CONFIG"
cmd="${{1:-}}"
shift || true
echo "$cmd $*" >> "{calls_log}"
case "$cmd" in
  --version) echo "meteorcli-stub 0"; exit 0 ;;
  config) echo "$*" > "$CONFIG/config-args"; exit 0 ;;
  test) exit 0 ;;
  request-token)
    printf '%s' '{{"request_id":"req-1","claim_secret":"clm_test"}}' > "$CONFIG/claim-secret"
    exit 0
    ;;
  claim)
    printf '%s' 'dev_test_token' > "$CONFIG/device-token"
    rm -f "$CONFIG/claim-secret"
    exit 0
    ;;
  *) exit 0 ;;
esac
""",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("METEOR")
        and key not in {"INVOCATION_ID", "SUDO_USER", "HOME"}
    }
    env.update(
        {
            "PATH": f"{stub_bin}{os.pathsep}{env.get('PATH', '')}",
            "METEORCLI_CONFIG_DIR": str(config_dir),
            "METEOR_FIRSTBOOT_ENV": str(env_file),
            "METEOR_FIRSTBOOT_UNIT": str(unit_path),
            "METEOR_FIRSTBOOT_SCRIPT": str(installed),
            "METEOR_FIRSTBOOT_ALLOW_NONROOT": "1",
            "METEOR_BOOT_DIR": str(boot),
            "METEOR_WAIT_SECONDS": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(installed), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    combined = result.stdout + result.stderr
    assert "Loaded enrollment settings from" in combined
    assert "already installed" in combined
    assert "key_from_boot" not in combined
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_test_token"
    assert "request-token" in calls_log.read_text(encoding="utf-8")
    assert "claim" in calls_log.read_text(encoding="utf-8")
    assert "--domain 192.168.0.118:8000" in (config_dir / "config-args").read_text(encoding="utf-8")

    systemctl_calls = systemctl_log.read_text(encoding="utf-8")
    assert "enable meteorcli.service" in systemctl_calls
    assert "start meteorcli.service" in systemctl_calls
    assert "disable meteorcli-firstboot.service" in systemctl_calls

    assert not installed.exists()
    assert not unit_path.exists()
    assert not env_file.exists()
    assert (config_dir / ".enrolled").is_file()


def test_poll_resumes_from_existing_claim_without_new_request(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / ".meteorenv").write_text(
        "METEOR_DOMAIN=192.168.0.118:8000\nMETEOR_API_KEY=key_from_boot\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / "etc" / "meteorcli"
    config_dir.mkdir(parents=True)
    (config_dir / "claim-secret").write_text(
        '{"request_id":"req-1","claim_secret":"clm_test"}',
        encoding="utf-8",
    )
    env_file = tmp_path / "etc" / "meteorcli-firstboot.env"
    unit_path = tmp_path / "unit.service"
    installed = tmp_path / "meteorcli-firstboot.sh"
    shutil.copy(FIRSTBOOT / "meteorcli-firstboot.sh", installed)
    installed.chmod(0o755)
    stub_bin = tmp_path / "stub-bin"
    calls_log = config_dir / "meteorcli-calls.log"
    _write_executable(stub_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        stub_bin / "systemctl",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        stub_bin / "meteorcli",
        f"""#!/usr/bin/env bash
cmd="${{1:-}}"
shift || true
echo "$cmd $*" >> "{calls_log}"
case "$cmd" in
  --version) echo "meteorcli-stub 0"; exit 0 ;;
  config|test) exit 0 ;;
  request-token) echo "should-not-request" >> "{calls_log}"; exit 1 ;;
  claim)
    printf '%s' 'dev_resumed' > "{config_dir}/device-token"
    rm -f "{config_dir}/claim-secret"
    exit 0
    ;;
  *) exit 0 ;;
esac
""",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("METEOR")
        and key not in {"INVOCATION_ID", "SUDO_USER", "HOME"}
    }
    env.update(
        {
            "PATH": f"{stub_bin}{os.pathsep}{env.get('PATH', '')}",
            "METEORCLI_CONFIG_DIR": str(config_dir),
            "METEOR_FIRSTBOOT_ENV": str(env_file),
            "METEOR_FIRSTBOOT_UNIT": str(unit_path),
            "METEOR_FIRSTBOOT_SCRIPT": str(installed),
            "METEOR_FIRSTBOOT_ALLOW_NONROOT": "1",
            "METEOR_BOOT_DIR": str(boot),
            "METEOR_WAIT_SECONDS": "1",
        }
    )
    result = subprocess.run(
        ["bash", str(installed), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    calls = calls_log.read_text(encoding="utf-8")
    assert "claim" in calls
    assert "request-token" not in calls
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_resumed"


def _run_migration(tmp_path: Path) -> tuple[Path, Path, subprocess.CompletedProcess]:
    system_dir = tmp_path / "etc-meteorcli"
    home = tmp_path / "home"
    user_dir = home / ".config" / "meteorcli"
    user_dir.mkdir(parents=True)
    (user_dir / "config.json").write_text('{"server_url": "http://old-cloud"}', encoding="utf-8")
    (user_dir / "api-key").write_text("key_old", encoding="utf-8")
    function = _installcli_function("migrate_user_config_to_system").replace("/etc/meteorcli", str(system_dir))
    result = subprocess.run(
        ["bash", "-c", f"{function}\nmigrate_user_config_to_system"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "SUDO_USER": ""},
        check=False,
    )
    return system_dir, user_dir, result


def test_migration_fills_an_empty_system_dir(tmp_path) -> None:
    system_dir, _, result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (system_dir / "api-key").read_text(encoding="utf-8") == "key_old"


def test_migration_keeps_a_system_dir_waiting_for_approval(tmp_path) -> None:
    # Configured and pending, but no device-token yet: a reinstall must not overwrite it.
    system_dir = tmp_path / "etc-meteorcli"
    system_dir.mkdir()
    (system_dir / "config.json").write_text('{"server_url": "http://new-cloud"}', encoding="utf-8")
    (system_dir / "api-key").write_text("key_new", encoding="utf-8")
    (system_dir / "claim-secret").write_text('{"request_id": "r", "claim_secret": "c"}', encoding="utf-8")
    _, _, result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Keeping existing" in result.stdout
    assert (system_dir / "api-key").read_text(encoding="utf-8") == "key_new"
    assert "new-cloud" in (system_dir / "config.json").read_text(encoding="utf-8")
