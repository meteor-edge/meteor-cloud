"""Sanity-check first-boot packaging files shipped for golden images."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
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
    (root / "boot" / "firmware" / ".meteorenv").write_text(
        "METEOR_WIFI_SSID=MyNetwork\nMETEOR_WIFI_PSK=wifi-password-123\nMETEOR_WIFI_COUNTRY=DE\n",
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

    assert result.returncode == 0, result.stdout + result.stderr
    wpa = (root / "etc" / "wpa_supplicant" / "wpa_supplicant.conf").read_text(encoding="utf-8")
    derived = hashlib.pbkdf2_hmac("sha1", b"wifi-password-123", b"MyNetwork", 4096, 32).hex()
    assert f"psk={derived}" in wpa
    assert "wifi-password-123" not in wpa
    assert "ssid=" + b"MyNetwork".hex() in wpa
    assert "country=DE" in wpa
    assert not (root / "etc" / "meteorcli-firstboot.env").exists()


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
