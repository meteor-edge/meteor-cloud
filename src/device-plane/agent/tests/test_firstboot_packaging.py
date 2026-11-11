"""Sanity-check first-boot packaging files shipped for golden images."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pytest
from pathlib import Path

FIRSTBOOT = Path(__file__).resolve().parents[1] / "packaging" / "firstboot"


def test_firstboot_script_is_valid_bash() -> None:
    for name in ("meteorcli-firstboot.sh", "install-meteorcli-firstboot.sh", "meteorcli-settings.sh"):
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
        if not key.startswith("METEOR") and key not in {"INVOCATION_ID", "SUDO_USER", "HOME"}
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
            "METEOR_START_JITTER_SECONDS": "0",
            "METEOR_FIRSTBOOT_LOCK": str(tmp_path / "firstboot.lock"),
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
        if not key.startswith("METEOR") and key not in {"INVOCATION_ID", "SUDO_USER", "HOME"}
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
            "METEOR_START_JITTER_SECONDS": "0",
            "METEOR_FIRSTBOOT_LOCK": str(tmp_path / "firstboot.lock"),
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


# ---------------------------------------------------------------- settings from the card


def _settings_env(tmp_path: Path, root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("METEOR")}
    env.update(
        {
            "METEOR_ROOT": str(root),
            "METEOR_SETTINGS_ALLOW_NONROOT": "1",
        }
    )
    return env


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "boot" / "firmware").mkdir(parents=True)
    (root / "etc" / "NetworkManager").mkdir(parents=True)
    wants = root / "etc" / "systemd" / "system" / "multi-user.target.wants"
    wants.mkdir(parents=True)
    (wants / "NetworkManager.service").symlink_to("/lib/systemd/system/NetworkManager.service")
    (root / "usr" / "share" / "zoneinfo" / "Europe").mkdir(parents=True)
    (root / "usr" / "share" / "zoneinfo" / "Europe" / "Berlin").write_text("TZif", encoding="utf-8")
    (root / "home" / "pi").mkdir(parents=True)
    (root / "etc" / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
        "pi:x:1000:1000:,,,:/home/pi:/bin/bash\n",
        encoding="utf-8",
    )
    (root / "etc" / "hostname").write_text("raspberrypi\n", encoding="utf-8")
    (root / "etc" / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi\n", encoding="utf-8")
    return root


SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyOnlyForTests user@laptop"


def test_settings_apply_wifi_hostname_timezone_ssh_and_leave_the_card(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    card = root / "boot" / "firmware" / ".meteorenv"
    card.write_text(
        "METEOR_ENV_VERSION=2\n"
        "METEOR_DOMAIN=fleet.example.com:8443\n"
        "METEOR_API_BASE=https://fleet.example.com:8443\n"
        "METEOR_API_KEY=key_secret-value\n"
        "METEOR_NAME=pi-lab-01\n"
        "METEOR_HOSTNAME=pi-lab-01\n"
        "METEOR_TIMEZONE=Europe/Berlin\n"
        'METEOR_WIFI_SSID=My Net; \\ "x"\n'
        "METEOR_WIFI_PSK=wifi-password-123\n"
        "METEOR_WIFI_COUNTRY=DE\n"
        "METEOR_WIFI_HIDDEN=true\n"
        f"METEOR_SSH_AUTHORIZED_KEY={SSH_KEY}\n"
        "METEOR_EVIL=$(touch /tmp/pwned)\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=_settings_env(tmp_path, root),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert not card.exists(), "the key and Wi-Fi password must leave the card"
    assert "key_secret-value" not in out and "wifi-password-123" not in out
    assert "ignoring unknown key METEOR_EVIL" in out

    env_file = root / "etc" / "meteorcli-firstboot.env"
    saved = env_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "METEOR_DOMAIN=fleet.example.com:8443\n" in saved
    assert "METEOR_API_KEY=key_secret-value\n" in saved
    assert "METEOR_API_BASE=https://fleet.example.com:8443\n" in saved
    assert "METEOR_NAME=pi-lab-01\n" in saved
    assert "WIFI" not in saved

    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "pi-lab-01\n"
    assert "127.0.1.1\tpi-lab-01" in (root / "etc" / "hosts").read_text(encoding="utf-8")
    assert os.readlink(root / "etc" / "localtime") == "/usr/share/zoneinfo/Europe/Berlin"
    assert (root / "etc" / "timezone").read_text(encoding="utf-8") == "Europe/Berlin\n"

    nm = root / "etc" / "NetworkManager" / "system-connections" / "meteor-wifi.nmconnection"
    conn = nm.read_text(encoding="utf-8")
    assert stat.S_IMODE(nm.stat().st_mode) == 0o600
    assert 'ssid=My Net; \\\\ "x"\n' in conn
    assert "psk=wifi-password-123\n" in conn
    assert "hidden=true\n" in conn
    assert "ieee80211_regdom=DE" in (root / "etc" / "modprobe.d" / "meteor-wifi-country.conf").read_text(
        encoding="utf-8"
    )

    keys = root / "home" / "pi" / ".ssh" / "authorized_keys"
    assert keys.read_text(encoding="utf-8") == SSH_KEY + "\n"
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600


def test_settings_without_networkmanager_write_wpa_supplicant_with_derived_key(tmp_path: Path) -> None:
    import hashlib

    root = _fake_root(tmp_path)
    shutil.rmtree(root / "etc" / "NetworkManager")
    shutil.rmtree(root / "etc" / "systemd")
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_WIFI_SSID=MyNetwork\nMETEOR_WIFI_PSK=wifi-password-123\nMETEOR_WIFI_COUNTRY=DE\n",
        encoding="utf-8",
    )
    # Raspberry Pi OS ships Wi-Fi blocked; systemd-rfkill restores this file at boot.
    rfkill = root / "var" / "lib" / "systemd" / "rfkill"
    rfkill.mkdir(parents=True)
    (rfkill / "platform-3f300000.mmcnr:wlan").write_text("1\n", encoding="utf-8")
    (rfkill / "platform-3f201000.serial:bluetooth").write_text("1\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=_settings_env(tmp_path, root),
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    wpa = (root / "etc" / "wpa_supplicant" / "wpa_supplicant.conf").read_text(encoding="utf-8")
    derived = hashlib.pbkdf2_hmac("sha1", b"wifi-password-123", b"MyNetwork", 4096, 32).hex()
    assert f"psk={derived}" in wpa
    assert "wifi-password-123" not in wpa
    assert "ssid=" + b"MyNetwork".hex() in wpa
    assert "country=DE" in wpa
    assert not (root / "etc" / "meteorcli-firstboot.env").exists()
    assert (rfkill / "platform-3f300000.mmcnr:wlan").read_text(encoding="utf-8") == "0\n"
    assert (rfkill / "platform-3f201000.serial:bluetooth").read_text(encoding="utf-8") == "1\n"


def test_settings_use_wpa_supplicant_when_networkmanager_is_installed_but_disabled(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "etc" / "systemd" / "system" / "multi-user.target.wants" / "NetworkManager.service").unlink()
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_WIFI_SSID=MyNetwork\nMETEOR_WIFI_PSK=wifi-password-123\n", encoding="utf-8"
    )

    result = _run_settings(tmp_path, root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "etc" / "wpa_supplicant" / "wpa_supplicant.conf").exists()
    assert not (root / "etc" / "NetworkManager" / "system-connections").exists()


def test_settings_keep_the_card_file_when_a_setting_cannot_be_written(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    card = root / "boot" / "firmware" / ".meteorenv"
    card.write_text(
        "METEOR_DOMAIN=fleet.example.com\nMETEOR_API_KEY=key_abc\n"
        "METEOR_HOSTNAME=pi-lab-01\nMETEOR_WIFI_SSID=MyNetwork\nMETEOR_WIFI_PSK=wifi-password-123\n",
        encoding="utf-8",
    )
    # A file where the connections directory should be: the Wi-Fi profile cannot be written.
    (root / "etc" / "NetworkManager" / "system-connections").write_text("", encoding="utf-8")

    result = _run_settings(tmp_path, root)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "warning: wifi failed" in out
    assert "Wi-Fi MyNetwork added" not in out
    assert card.exists(), "the only copy of the Wi-Fi settings must stay for the next boot"
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "pi-lab-01\n"
    assert "METEOR_API_KEY=key_abc" in (root / "etc" / "meteorcli-firstboot.env").read_text(encoding="utf-8")
    assert "wifi-password-123" not in out


def test_settings_skip_invalid_values_but_keep_enrollment(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_DOMAIN=fleet.example.com\n"
        "METEOR_API_KEY=key_abc\n"
        "METEOR_HOSTNAME=Bad_Name!\n"
        "METEOR_TIMEZONE=../../etc/shadow\n"
        "METEOR_WIFI_SSID=Net\n"
        "METEOR_WIFI_PSK=short\n"
        "METEOR_SSH_AUTHORIZED_KEY=not a key\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=_settings_env(tmp_path, root),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "raspberrypi\n"
    assert not (root / "etc" / "localtime").exists()
    assert not (root / "etc" / "NetworkManager" / "system-connections").exists()
    assert not (root / "home" / "pi" / ".ssh").exists()
    assert "METEOR_API_KEY=key_abc" in (root / "etc" / "meteorcli-firstboot.env").read_text(encoding="utf-8")
    assert out.count("warning:") == 4


def test_settings_on_an_enrolled_device_do_not_keep_the_key(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "etc" / "meteorcli").mkdir(parents=True)
    (root / "etc" / "meteorcli" / "device-token").write_text("dev_token", encoding="utf-8")
    card = root / "boot" / "firmware" / ".meteorenv"
    card.write_text(
        "METEOR_DOMAIN=fleet.example.com\nMETEOR_API_KEY=key_abc\nMETEOR_HOSTNAME=pi-moved\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=_settings_env(tmp_path, root),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "already enrolled" in out
    # Other settings still apply, the key is not written anywhere and leaves the card.
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "pi-moved\n"
    assert not (root / "etc" / "meteorcli-firstboot.env").exists()
    assert not card.exists()
    assert "key_abc" not in out


def test_settings_do_nothing_without_a_settings_file(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=_settings_env(tmp_path, root),
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "raspberrypi\n"


PASSWORD_HASH = "$6$saltsalt$" + "a" * 86


def _command_stubs(tmp_path: Path, *names: str) -> tuple[Path, Path]:
    """Stub commands that only record how they were called."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    for name in names:
        _write_executable(stub_bin / name, f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\nexit 0\n')
    return stub_bin, calls


def _run_settings(tmp_path: Path, root: Path, stub_bin: Path | None = None) -> subprocess.CompletedProcess:
    env = _settings_env(tmp_path, root)
    if stub_bin is not None:
        env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-settings.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_settings_rename_the_first_user_set_its_password_and_ssh(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "etc" / "group").write_text("sudo:x:27:pi\npi:x:1000:\n", encoding="utf-8")
    sudoers = root / "etc" / "sudoers.d" / "010_pi-nopasswd"
    sudoers.parent.mkdir(parents=True)
    sudoers.write_text("pi ALL=(ALL) NOPASSWD: ALL\n", encoding="utf-8")
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_ENV_VERSION=2\n"
        "METEOR_USER=ops\n"
        f"METEOR_USER_PASSWORD_HASH={PASSWORD_HASH}\n"
        "METEOR_SSH_ENABLED=true\n"
        "METEOR_SSH_PASSWORD_AUTH=false\n",
        encoding="utf-8",
    )
    stub_bin, calls = _command_stubs(tmp_path, "usermod", "groupmod", "useradd")

    result = _run_settings(tmp_path, root, stub_bin)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert PASSWORD_HASH not in out
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "usermod -l ops -d /home/ops -m pi",
        "groupmod -n ops pi",
        f"usermod -p {PASSWORD_HASH} ops",
    ]
    assert sudoers.read_text(encoding="utf-8") == "ops ALL=(ALL) NOPASSWD: ALL\n"
    dropin = root / "etc" / "ssh" / "sshd_config.d" / "10-meteor.conf"
    assert "PasswordAuthentication no\n" in dropin.read_text(encoding="utf-8")
    assert "SSH enabled" in out


def test_settings_create_the_user_when_the_image_has_none(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
    (root / "etc" / "group").write_text("sudo:x:27:\nvideo:x:44:\ndocker:x:990:\n", encoding="utf-8")
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        f"METEOR_USER=ops\nMETEOR_USER_PASSWORD_HASH={PASSWORD_HASH}\nMETEOR_SSH_PASSWORD_AUTH=true\n",
        encoding="utf-8",
    )
    stub_bin, calls = _command_stubs(tmp_path, "usermod", "groupmod", "useradd")

    result = _run_settings(tmp_path, root, stub_bin)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "useradd -m -s /bin/bash -G sudo,video,docker ops",
        f"usermod -p {PASSWORD_HASH} ops",
    ]
    dropin = root / "etc" / "ssh" / "sshd_config.d" / "10-meteor.conf"
    assert "PasswordAuthentication yes\n" in dropin.read_text(encoding="utf-8")


def test_settings_refuse_a_plain_text_password_and_bad_user(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_USER=pi\nMETEOR_USER_PASSWORD_HASH=plain-password\n", encoding="utf-8"
    )
    stub_bin, calls = _command_stubs(tmp_path, "usermod", "groupmod", "useradd")

    result = _run_settings(tmp_path, root, stub_bin)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "not a crypt hash" in out
    assert "plain-password" not in out
    assert not calls.exists()

    (root / "boot" / "firmware" / ".meteorenv").write_text("METEOR_USER=root\n", encoding="utf-8")
    result = _run_settings(tmp_path, root, stub_bin)
    assert "METEOR_USER is not a valid user name" in result.stdout
    assert not calls.exists()


def test_settings_create_missing_ssh_host_keys_and_grow_once(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    sshd = root / "usr" / "sbin" / "sshd"
    sshd.parent.mkdir(parents=True)
    _write_executable(sshd, "#!/bin/sh\n")
    (root / "etc" / "meteorcli-expand-rootfs").write_text("", encoding="utf-8")
    stub_bin, calls = _command_stubs(tmp_path, "ssh-keygen")

    result = _run_settings(tmp_path, root, stub_bin)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8") == f"ssh-keygen -A -f {root}\n"
    assert not (root / "etc" / "meteorcli-expand-rootfs").exists()

    (root / "etc" / "ssh").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "ssh" / "ssh_host_ed25519_key").write_text("key", encoding="utf-8")
    calls.unlink()
    result = _run_settings(tmp_path, root, stub_bin)
    assert result.returncode == 0
    assert not calls.exists()


def _prepared_device(tmp_path: Path) -> Path:
    root = _fake_root(tmp_path)
    boot = root / "boot" / "firmware"
    (boot / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=abc-02 rootwait ds=nocloud;i=meteor-123\n", encoding="utf-8"
    )
    (boot / "user-data").write_text("#cloud-config\nusers:\n- name: pi\n  passwd: secret-hash\n", encoding="utf-8")
    (boot / "network-config").write_text("wifis: {password: wifi-password-123}\n", encoding="utf-8")
    (boot / "meta-data").write_text("instance-id: meteor-123\n", encoding="utf-8")
    for path in (
        "usr/local/sbin/meteorcli-settings.sh",
        "usr/local/sbin/meteorcli-firstboot.sh",
        "etc/systemd/system/meteorcli-firstboot.service",
    ):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("installed", encoding="utf-8")
    meteorcli = root / "etc" / "meteorcli"
    (meteorcli / "wireguard").mkdir(parents=True)
    (meteorcli / "device-token").write_text("dev_token", encoding="utf-8")
    (meteorcli / "wireguard" / "wg0.conf").write_text("PrivateKey=x", encoding="utf-8")
    (root / "etc" / "meteorcli-firstboot.env").write_text("METEOR_API_KEY=key_abc\n", encoding="utf-8")
    (root / "home" / "pi" / ".config" / "meteorcli").mkdir(parents=True)
    (root / "home" / "pi" / ".bash_history").write_text("sudo something\n", encoding="utf-8")
    nm = root / "etc" / "NetworkManager" / "system-connections"
    nm.mkdir(parents=True)
    (nm / "home.nmconnection").write_text("[connection]\ntype=wifi\n[wifi-security]\npsk=x\n", encoding="utf-8")
    (nm / "lan.nmconnection").write_text("[connection]\ntype=ethernet\n", encoding="utf-8")
    (root / "etc" / "netplan").mkdir(parents=True)
    (root / "etc" / "netplan" / "50-cloud-init.yaml").write_text("wifis: {}\n", encoding="utf-8")
    (root / "etc" / "cloud").mkdir(parents=True)
    (root / "var" / "lib" / "cloud" / "instance").mkdir(parents=True)
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "etc" / "ssh" / "ssh_host_ed25519_key").write_text("private", encoding="utf-8")
    (root / "etc" / "ssh" / "sshd_config").write_text("Include /etc/ssh/sshd_config.d/*.conf\n", encoding="utf-8")
    (root / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    (root / "var" / "log").mkdir(parents=True)
    (root / "var" / "log" / "syslog.1").write_text("old", encoding="utf-8")
    (root / "var" / "log" / "meteorcli-settings.log").write_text("Applying", encoding="utf-8")
    return root


def test_prepare_image_removes_this_devices_identity(tmp_path: Path) -> None:
    root = _prepared_device(tmp_path)
    env = {key: value for key, value in os.environ.items() if not key.startswith("METEOR")}
    env.update({"METEOR_ROOT": str(root), "METEOR_PREPARE_ALLOW_NONROOT": "1"})

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-prepare-image.sh"), "--yes", "--no-poweroff"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "this device is enrolled" in out
    boot = root / "boot" / "firmware"
    assert (boot / ".meteor-settings").read_text(encoding="utf-8") == "meteor_settings=2\n"
    assert (boot / "user-data").read_text(encoding="utf-8") == "#cloud-config\n"
    assert not (boot / "network-config").exists()
    assert (boot / "meta-data").exists()
    assert "ds=nocloud" not in (boot / "cmdline.txt").read_text(encoding="utf-8")
    assert "rootwait" in (boot / "cmdline.txt").read_text(encoding="utf-8")

    assert list((root / "etc" / "meteorcli").iterdir()) == []
    assert not (root / "etc" / "meteorcli-firstboot.env").exists()
    assert not (root / "home" / "pi" / ".config" / "meteorcli").exists()
    assert not (root / "home" / "pi" / ".bash_history").exists()

    nm = root / "etc" / "NetworkManager" / "system-connections"
    assert sorted(p.name for p in nm.iterdir()) == ["lan.nmconnection"]
    assert not (root / "etc" / "netplan" / "50-cloud-init.yaml").exists()
    assert (root / "etc" / "cloud" / "cloud-init.disabled").exists()
    assert not (root / "var" / "lib" / "cloud").exists()

    assert not list((root / "etc" / "ssh").glob("ssh_host_*"))
    assert (root / "etc" / "ssh" / "sshd_config").exists()
    assert (root / "etc" / "machine-id").read_text(encoding="utf-8") == ""
    assert (root / "etc" / "meteorcli-expand-rootfs").exists()
    assert not (root / "var" / "log" / "syslog.1").exists()
    assert (root / "var" / "log" / "meteorcli-settings.log").read_text(encoding="utf-8") == ""


def test_prepare_image_keep_wifi_and_requires_confirmation(tmp_path: Path) -> None:
    root = _prepared_device(tmp_path)
    env = {key: value for key, value in os.environ.items() if not key.startswith("METEOR")}
    env.update({"METEOR_ROOT": str(root), "METEOR_PREPARE_ALLOW_NONROOT": "1"})
    script = str(FIRSTBOOT / "meteorcli-prepare-image.sh")

    refused = subprocess.run(
        ["bash", script], check=False, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30
    )
    assert refused.returncode == 1
    assert "pass --yes" in refused.stderr
    assert (root / "etc" / "meteorcli" / "device-token").exists()

    kept = subprocess.run(
        ["bash", script, "--yes", "--no-poweroff", "--keep-wifi"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert (root / "etc" / "NetworkManager" / "system-connections" / "home.nmconnection").exists()


def test_prepare_image_is_released_and_installs_first_boot_for_images() -> None:
    subprocess.run(["bash", "-n", str(FIRSTBOOT / "meteorcli-prepare-image.sh")], check=True)
    dist = (Path(__file__).resolve().parents[4] / "scripts" / "agent-dist.sh").read_text(encoding="utf-8")
    assert "meteorcli-prepare-image.sh" in dist
    prepare = (FIRSTBOOT / "meteorcli-prepare-image.sh").read_text(encoding="utf-8")
    assert "install-meteorcli-firstboot.sh" in prepare and "--image" in prepare
    installer = (FIRSTBOOT / "install-meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "--image)" in installer


def test_settings_unit_runs_before_network_and_enrollment() -> None:
    unit = (FIRSTBOOT / "meteorcli-settings.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "DefaultDependencies=no" in unit
    for target in ("network-pre.target", "NetworkManager.service", "meteorcli-firstboot.service"):
        assert target in unit.split("Before=", 1)[1].splitlines()[0]
    installer = (FIRSTBOOT / "install-meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "meteorcli-settings.sh" in installer
    assert "systemctl enable meteorcli-settings.service" in installer
    assert ".meteor-settings" in installer


# ---------------------------------------------------------------- request flooding


def _poll_env(tmp_path: Path, stub_bin: Path, config_dir: Path, boot: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("METEOR") and key not in {"INVOCATION_ID", "SUDO_USER", "HOME"}
    }
    env.update(
        {
            "PATH": f"{stub_bin}{os.pathsep}{env.get('PATH', '')}",
            "METEORCLI_CONFIG_DIR": str(config_dir),
            "METEOR_FIRSTBOOT_ENV": str(tmp_path / "firstboot.env"),
            "METEOR_FIRSTBOOT_UNIT": str(tmp_path / "unit.service"),
            "METEOR_FIRSTBOOT_SCRIPT": str(tmp_path / "meteorcli-firstboot.sh"),
            "METEOR_FIRSTBOOT_ALLOW_NONROOT": "1",
            "METEOR_BOOT_DIR": str(boot),
            "METEOR_WAIT_SECONDS": "1",
            "METEOR_START_JITTER_SECONDS": "0",
            "METEOR_RETRY_BASE_SECONDS": "1",
            "METEOR_RETRY_MAX_SECONDS": "2",
            "METEOR_FIRSTBOOT_LOCK": str(tmp_path / "firstboot.lock"),
        }
    )
    return env


def _poll_fixture(tmp_path: Path, request_token_body: str) -> tuple[Path, Path, Path, Path]:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / ".meteorenv").write_text("METEOR_DOMAIN=fleet.example.com\nMETEOR_API_KEY=key_abc\n", encoding="utf-8")
    config_dir = tmp_path / "etc" / "meteorcli"
    config_dir.mkdir(parents=True)
    stub_bin = tmp_path / "stub-bin"
    calls_log = tmp_path / "calls.log"
    _write_executable(stub_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(stub_bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(stub_bin / "sleep", f'#!/usr/bin/env bash\necho "sleep $*" >> "{calls_log}"\n')
    _write_executable(
        stub_bin / "meteorcli",
        f"""#!/usr/bin/env bash
cmd="${{1:-}}"
shift || true
echo "$cmd" >> "{calls_log}"
case "$cmd" in
  --version) echo "meteorcli-stub 0" ;;
  config|test) exit 0 ;;
  request-token)
{request_token_body}
    ;;
  claim)
    printf '%s' 'dev_ok' > "{config_dir}/device-token"
    rm -f "{config_dir}/claim-secret"
    ;;
esac
""",
    )
    return boot, config_dir, stub_bin, calls_log


def test_wrong_key_stops_instead_of_retrying(tmp_path: Path) -> None:
    boot, config_dir, stub_bin, calls_log = _poll_fixture(
        tmp_path, '    echo "error: enrollment request failed: The enrollment API key has expired." >&2\n    exit 3'
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=_poll_env(tmp_path, stub_bin, config_dir, boot),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out  # 0 so systemd's Restart=on-failure does not retry
    assert "rejected the enrollment key" in out
    assert calls_log.read_text(encoding="utf-8").count("request-token") == 1
    assert not (config_dir / "device-token").exists()


def test_busy_server_gets_backoff_and_retry_after(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    body = f"""    n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{counter}"
    if [ "$n" -lt 3 ]; then
      echo "error: enrollment request failed: Too many" >&2; echo "retry-after: 7" >&2; exit 75
    fi
    printf '%s' '{{"request_id":"r","claim_secret":"c"}}' > "{tmp_path}/etc/meteorcli/claim-secret"
"""
    boot, config_dir, stub_bin, calls_log = _poll_fixture(tmp_path, body)

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=_poll_env(tmp_path, stub_bin, config_dir, boot),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    calls = calls_log.read_text(encoding="utf-8")
    assert calls.count("request-token") == 3
    # The server's Retry-After (7 s) wins over the 1-2 s backoff.
    assert calls.count("sleep 7") == 2
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_ok"


def test_pending_poll_that_returns_at_once_backs_off(tmp_path: Path) -> None:
    """claim coming back immediately (server down, short wait) must not spin the loop."""
    boot, config_dir, stub_bin, calls_log = _poll_fixture(tmp_path, "    exit 1")
    (config_dir / "claim-secret").write_text('{"request_id":"r","claim_secret":"c"}', encoding="utf-8")
    counter = tmp_path / "claims"
    _write_executable(
        stub_bin / "meteorcli",
        f"""#!/usr/bin/env bash
cmd="${{1:-}}"
echo "$cmd" >> "{calls_log}"
case "$cmd" in
  --version) echo "meteorcli-stub 0" ;;
  claim)
    n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{counter}"
    if [ "$n" -lt 4 ]; then echo "error: poll failed: connection refused" >&2; exit 1; fi
    printf '%s' 'dev_ok' > "{config_dir}/device-token"
    rm -f "{config_dir}/claim-secret"
    ;;
esac
""",
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=_poll_env(tmp_path, stub_bin, config_dir, boot),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("claim") == 4
    assert "request-token" not in calls
    # One pause after every early return, growing up to the cap.
    sleeps = [int(line.split()[1]) for line in calls if line.startswith("sleep ")]
    assert len(sleeps) == 3
    assert all(seconds >= 1 for seconds in sleeps)
    assert sleeps[-1] >= 2


def test_claim_collected_by_the_service_does_not_send_a_second_request(tmp_path: Path) -> None:
    """meteorcli.service can take the token while `claim` waits: claim then drops the
    claim secret and fails just before the service has written the device token."""
    boot, config_dir, stub_bin, calls_log = _poll_fixture(tmp_path, "    exit 1")
    (config_dir / "claim-secret").write_text('{"request_id":"r","claim_secret":"c"}', encoding="utf-8")
    real_sleep = shutil.which("sleep")
    _write_executable(
        stub_bin / "meteorcli",
        f"""#!/usr/bin/env bash
cmd="${{1:-}}"
echo "$cmd" >> "{calls_log}"
case "$cmd" in
  --version) echo "meteorcli-stub 0" ;;
  claim)
    # A full wait (not an early return), then: approved, but collected elsewhere.
    "{real_sleep}" 1.2
    rm -f "{config_dir}/claim-secret"
    echo "Approved, but the device credential was already claimed." >&2
    exit 1
    ;;
esac
""",
    )
    # The service finishes writing the token during first boot's short pause.
    _write_executable(
        stub_bin / "sleep",
        f"""#!/usr/bin/env bash
echo "sleep $*" >> "{calls_log}"
printf '%s' 'dev_ok' > "{config_dir}/device-token"
""",
    )

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=_poll_env(tmp_path, stub_bin, config_dir, boot),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("claim") == 1
    assert "request-token" not in calls
    assert "sleep 5" in calls
    assert not (tmp_path / "unit.service").exists()


def test_rejected_request_is_resubmitted_after_the_pause(tmp_path: Path) -> None:
    """A request that ended without a token (rejected, expired) is still asked for again."""
    body = f"    printf '%s' 'dev_ok' > '{tmp_path}/etc/meteorcli/device-token'"
    boot, config_dir, stub_bin, calls_log = _poll_fixture(tmp_path, body)
    (config_dir / "claim-secret").write_text('{"request_id":"r","claim_secret":"c"}', encoding="utf-8")
    stub = (stub_bin / "meteorcli").read_text(encoding="utf-8")
    start = stub.index("  claim)")
    stub = stub[:start] + f'  claim)\n    rm -f "{config_dir}/claim-secret"\n    exit 1\n    ;;\nesac\n'
    _write_executable(stub_bin / "meteorcli", stub)

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=_poll_env(tmp_path, stub_bin, config_dir, boot),
        timeout=30,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("request-token") == 1
    assert calls.index("sleep 5") < calls.index("request-token")


def test_card_without_a_key_stops_instead_of_restarting_forever(tmp_path: Path) -> None:
    boot, config_dir, stub_bin, calls_log = _poll_fixture(tmp_path, "    exit 1")
    (boot / ".meteorenv").unlink()
    home = tmp_path / "home"
    home.mkdir()
    env = _poll_env(tmp_path, stub_bin, config_dir, boot)
    env["HOME"] = str(home)

    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=home,
        timeout=60,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out  # 0 so systemd's Restart=on-failure does not loop
    assert "will not join MeteorCloud by itself" in out
    assert "request-token" not in calls_log.read_text(encoding="utf-8")

    # A settings file with placeholders (the interactive installer) still retries.
    (tmp_path / "firstboot.env").write_text(
        "METEOR_DOMAIN=cloud.example.com\nMETEOR_API_KEY=key_replace_me\n", encoding="utf-8"
    )
    result = subprocess.run(
        ["bash", str(FIRSTBOOT / "meteorcli-firstboot.sh"), "--poll"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=home,
        timeout=60,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_retry_delay_doubles_up_to_the_cap() -> None:
    src = (FIRSTBOOT / "meteorcli-firstboot.sh").read_text(encoding="utf-8")
    start = src.index("retry_delay() {")
    end = src.index("\n}\n", start) + 3
    script = (
        f"RETRY_BASE_SECONDS=10\nRETRY_MAX_SECONDS=300\n{src[start:end]}\n"
        + "for n in 1 2 3 4 5 6 7 8; do retry_delay $n; done"
    )
    delays = [
        int(v)
        for v in subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout.split()
    ]
    bases = [10, 20, 40, 80, 160, 300, 300, 300]
    for delay, base in zip(delays, bases, strict=True):
        assert base <= delay <= base + base // 4


# What the MeteorCloud console downloads (control plane: app/devices/device_config.py) and
# Meteor Imager writes (MeteorEnvV2). The control-plane test renders exactly this text.
CONSOLE_SETTINGS_FILE = (
    "METEOR_ENV_VERSION=2\n"
    "METEOR_DOMAIN=fleet.example.com:8443\n"
    "METEOR_API_KEY=key_secret-value\n"
    "METEOR_NAME=Lobby kiosk\n"
    "METEOR_HOSTNAME=pi-lab-01\n"
    "METEOR_TIMEZONE=Europe/Berlin\n"
    'METEOR_WIFI_SSID=My Net; \\ "x"\n'
    "METEOR_WIFI_PSK=wifi-password-123\n"
    "METEOR_WIFI_COUNTRY=DE\n"
    "METEOR_WIFI_HIDDEN=false\n"
    "METEOR_USER=pi\n"
    "METEOR_USER_PASSWORD_HASH=$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1\n"
    "METEOR_SSH_ENABLED=true\n"
    "METEOR_SSH_PASSWORD_AUTH=false\n"
    f"METEOR_SSH_AUTHORIZED_KEY={SSH_KEY}\n"
)


@pytest.mark.parametrize("file_name", ["meteorenv.txt", ".meteorenv", "meteorenv"])
def test_settings_file_from_the_console_applies_like_the_imagers(tmp_path: Path, file_name: str) -> None:
    root = _fake_root(tmp_path)
    card = root / "boot" / "firmware" / file_name
    # A Windows editor's byte order mark and line endings must not matter.
    card.write_bytes(b"\xef\xbb\xbf" + CONSOLE_SETTINGS_FILE.replace("\n", "\r\n").encode())

    stub_bin, calls = _command_stubs(tmp_path, "usermod", "groupmod", "useradd")
    result = _run_settings(tmp_path, root, stub_bin)

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert calls.read_text(encoding="utf-8").splitlines()[-1].startswith("usermod -p $6$saltstring$svn8UoSV")
    assert "ignoring unknown key" not in out
    assert "warning" not in out, out
    assert not card.exists(), "the settings file (key, Wi-Fi password) must leave the card"
    saved = (root / "etc" / "meteorcli-firstboot.env").read_text(encoding="utf-8")
    assert "METEOR_DOMAIN=fleet.example.com:8443\n" in saved
    assert "METEOR_API_KEY=key_secret-value\n" in saved
    assert "METEOR_NAME=Lobby kiosk\n" in saved
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "pi-lab-01\n"
    wifi = (root / "etc" / "NetworkManager" / "system-connections" / "meteor-wifi.nmconnection").read_text(
        encoding="utf-8"
    )
    assert "psk=wifi-password-123" in wifi
    assert "wifi-password-123" not in out and "key_secret-value" not in out
