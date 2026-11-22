"""The first-boot scripts shipped with the Go meteorcli release (packaging/firstboot).

meteorcli-settings.sh applies a card's .meteorenv (Wi-Fi, host name, user, SSH)
early in boot and hands the enrollment lines to meteorcli in /etc/meteorcli/meteorenv;
meteorcli-prepare-image.sh turns a device into an OS image. Both run here against a
fake root (METEOR_ROOT), with stub commands.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

FIRSTBOOT = Path(__file__).resolve().parents[2] / "src" / "device-plane" / "agent-go" / "packaging" / "firstboot"


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)




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

    env_file = root / "etc" / "meteorcli" / "meteorenv"
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
    assert not (root / "etc" / "meteorcli" / "meteorenv").exists()
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
    assert "METEOR_API_KEY=key_abc" in (root / "etc" / "meteorcli" / "meteorenv").read_text(encoding="utf-8")
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
    assert "METEOR_API_KEY=key_abc" in (root / "etc" / "meteorcli" / "meteorenv").read_text(encoding="utf-8")
    assert out.count("warning:") == 4


def test_settings_on_an_enrolled_device_do_not_keep_the_key(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    (root / "var" / "lib" / "meteorcli" / "identity").mkdir(parents=True)
    (root / "var" / "lib" / "meteorcli" / "identity" / "device-token").write_text("dev_token", encoding="utf-8")
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
    assert not (root / "etc" / "meteorcli" / "meteorenv").exists()
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
        "usr/local/bin/meteorcli",
        "etc/systemd/system/meteorcli.service",
        # From an image prepared for the Python agent: removed.
        "usr/local/sbin/meteorcli-firstboot.sh",
        "etc/systemd/system/meteorcli-firstboot.service",
    ):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("installed", encoding="utf-8")
    (root / "usr" / "local" / "bin" / "meteorcli").chmod(0o755)
    etc = root / "etc" / "meteorcli"
    (etc / "docker").mkdir(parents=True)
    (etc / "docker" / "config.json").write_text("{}", encoding="utf-8")
    (etc / "config.json").write_text(
        '{\n  "version": 1,\n  "domain": "fleet.example.com",\n  "ssh_user": "pi"\n}\n', encoding="utf-8"
    )
    (etc / "meteorenv").write_text("METEOR_API_KEY=key_abc\n", encoding="utf-8")
    state = root / "var" / "lib" / "meteorcli"
    (state / "identity").mkdir(parents=True)
    (state / "identity" / "device-token").write_text("dev_token", encoding="utf-8")
    (state / "identity" / "api-key").write_text("key_abc", encoding="utf-8")
    (state / "state").mkdir()
    (state / "state" / "deploy.json").write_text("{}", encoding="utf-8")
    wg = root / "var" / "lib" / "meteorcli-helper" / "wireguard"
    wg.mkdir(parents=True)
    (wg / "wg0.conf").write_text("PrivateKey=x", encoding="utf-8")
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

    etc = root / "etc" / "meteorcli"
    assert sorted(p.name for p in etc.iterdir()) == ["config.json", "docker"]
    config = (etc / "config.json").read_text(encoding="utf-8")
    assert '"ssh_user": "pi"' in config and "fleet.example.com" not in config
    assert (etc / "docker" / "config.json").exists()
    assert list((root / "var" / "lib" / "meteorcli" / "identity").iterdir()) == []
    assert list((root / "var" / "lib" / "meteorcli" / "state").iterdir()) == []
    assert not (root / "var" / "lib" / "meteorcli-helper" / "wireguard").exists()
    assert not (root / "etc" / "systemd" / "system" / "meteorcli-firstboot.service").exists()
    assert (root / "etc" / "systemd" / "system" / "meteorcli.service").exists()
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
    assert (root / "var" / "lib" / "meteorcli" / "identity" / "device-token").exists()

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
    dist = (FIRSTBOOT.parents[1] / "scripts" / "dist.sh").read_text(encoding="utf-8")
    assert "meteorcli-prepare-image.sh" in dist and "install-meteorcli-firstboot.sh" in dist
    prepare = (FIRSTBOOT / "meteorcli-prepare-image.sh").read_text(encoding="utf-8")
    assert "install-meteorcli-firstboot.sh" in prepare and "--image" in prepare
    installer = (FIRSTBOOT / "install-meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "--image)" in installer


def test_settings_unit_runs_before_network_and_the_agent() -> None:
    unit = (FIRSTBOOT / "meteorcli-settings.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "DefaultDependencies=no" in unit
    for target in ("network-pre.target", "NetworkManager.service", "meteorcli.service"):
        assert target in unit.split("Before=", 1)[1].splitlines()[0]
    installer = (FIRSTBOOT / "install-meteorcli-firstboot.sh").read_text(encoding="utf-8")
    assert "meteorcli-settings.sh" in installer
    assert "systemctl enable meteorcli-settings.service" in installer
    assert ".meteor-settings" in installer
    # Agent must not start during install-meteorcli.sh: that would delete the card's
    # .meteorenv before meteorcli-settings.service applies Wi-Fi and user settings.
    assert 'args=(--no-start)' in installer
    assert "systemctl start meteorcli-settings.service" in installer
    assert "systemctl enable --now meteorcli-helper.socket meteorcli.service" in installer


# ---------------------------------------------------------------- request flooding



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
    saved = (root / "etc" / "meteorcli" / "meteorenv").read_text(encoding="utf-8")
    assert "METEOR_DOMAIN=fleet.example.com:8443\n" in saved
    assert "METEOR_API_KEY=key_secret-value\n" in saved
    assert "METEOR_NAME=Lobby kiosk\n" in saved
    assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "pi-lab-01\n"
    wifi = (root / "etc" / "NetworkManager" / "system-connections" / "meteor-wifi.nmconnection").read_text(
        encoding="utf-8"
    )
    assert "psk=wifi-password-123" in wifi
    assert "wifi-password-123" not in out and "key_secret-value" not in out
