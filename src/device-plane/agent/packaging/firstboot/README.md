# First-boot enrollment

Installs `meteorcli` on first boot, submits `request-token`, then **polls in the
background** (`meteorcli-firstboot.service`) until an administrator approves,
starts `meteorcli.service`, and **removes first-boot** so the next reboot only
runs the agent.

Running `sudo ./meteorcli-firstboot.sh` over SSH does **not** wait in that
terminal for approval. You can close the session; watch
`journalctl -u meteorcli-firstboot -f`.

Repo: [github.com/meteor-edge/meteor-cloud](https://github.com/meteor-edge/meteor-cloud)

This is Path B (enrollment API key). The key does not join the fleet by itself.

## One command (golden image or a live Pi)

```bash
# From the repo (works before the next agent release):
curl -fsSL https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh | sudo bash

# Or from a GitHub Release (after tag agent-v*):
# curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli-firstboot.sh | sudo bash
```

With domain and key already known (environment, sudo line, or a `.meteorenv` file):

```bash
# Option A: environment (the script also reads these from the shell that ran sudo)
export METEOR_DOMAIN=192.168.0.118:8000
export METEOR_API_KEY=key_...
sudo ./meteorcli-firstboot.sh

# Option B: same line as sudo
sudo METEOR_DOMAIN=192.168.0.118:8000 METEOR_API_KEY=key_... ./meteorcli-firstboot.sh

# Option C: .meteorenv next to the script, in the current directory, or in $HOME
cat > .meteorenv <<'EOF'
METEOR_DOMAIN=192.168.0.118:8000
METEOR_API_KEY=key_...
EOF
chmod 0600 .meteorenv
sudo ./meteorcli-firstboot.sh

# Option D: empty image + boot partition (see numbered steps below).
#   /boot/firmware/.meteorenv   (Bookworm+)
#   /boot/.meteorenv            (older images)
```

## Empty Raspberry Pi OS image (automatic, no SSH, no cmdline)

Current Raspberry Pi OS (bootfs has `user-data`) runs cloud-init on first boot.
Do **not** edit `cmdline.txt`. `.meteorenv` alone is not enough.

1. Flash the OS. The laptop shows **boot** / **bootfs**.
2. Copy `.meteorenv` onto that drive:

```text
METEOR_DOMAIN=192.168.0.118:8000
METEOR_API_KEY=key_...
```

Use the address the **Pi** can reach (laptop LAN IP, not `localhost`).
3. Open `user-data` on the same drive. Keep the first line `#cloud-config`.
   Append the `packages:` and `runcmd:` block from `cloud-init-user-data.yaml`
   (or replace the file with that yaml).
4. Eject, boot the Pi. Wait for network. Approve the pending device in the
   console. No SSH.

Fallback if you can already SSH: `curl …/install-meteorcli-firstboot.sh | sudo bash -s -- --now`.
`cmdline.txt` + `firstrun.sh` is a last resort.

The installer does not need the domain/key in the environment; first-boot
reads them from the boot partition. It copies the script and unit, submits
the pending request, and polls until approve. Then `meteorcli.service` stays
enabled and first-boot is deleted. Reboot is not required after approve.

`METEOR_DOMAIN` is the control-plane host (`cloud.example.com` or
`192.168.0.118:8000` for laptop `make dev`).

## Settings on the card (`.meteorenv` v2, Meteor Imager)

A base image with meteorcli and first-boot installed (`install-meteorcli-firstboot.sh`)
also gets `meteorcli-settings.service`. Early in boot, before the network starts, it reads
`.meteorenv` from the card's config partition, applies the settings, moves the enrollment
values to `/etc/meteorcli-firstboot.env` (root only) and **deletes `.meteorenv` from the
card**. Without `.meteorenv` it does nothing. Log: `/var/log/meteorcli-settings.log`.

Config partition, first match: a FAT partition labelled `METEORCFG` (for Jetson, NUC and
other device types), `/boot/firmware` (Raspberry Pi OS Bookworm+), `/boot`, `/boot/efi`.
The installer writes `.meteor-settings` there, which tells Meteor Imager to write only
`.meteorenv` and leave cloud-init alone.

```text
METEOR_ENV_VERSION=2
METEOR_DOMAIN=fleet.example.com:8443
METEOR_API_BASE=https://fleet.example.com:8443    # optional: when the scheme matters
METEOR_API_KEY=key_...
METEOR_NAME=pi-lab-01                             # optional: name in MeteorCloud
METEOR_HOSTNAME=pi-lab-01                         # optional
METEOR_TIMEZONE=Europe/Berlin                     # optional
METEOR_WIFI_SSID=My Network                       # optional, with PSK and COUNTRY
METEOR_WIFI_PSK=wifi-password
METEOR_WIFI_COUNTRY=DE
METEOR_WIFI_HIDDEN=false
METEOR_USER=ops                                   # optional: login user
METEOR_USER_PASSWORD_HASH=$6$salt$hash            # optional: crypt hash, never plain text
METEOR_SSH_ENABLED=true                           # optional: true / false
METEOR_SSH_PASSWORD_AUTH=false                    # optional: false = keys only
METEOR_SSH_AUTHORIZED_KEY=ssh-ed25519 AAAA... me@laptop   # optional
METEOR_SSH_USER=pi                                # optional: default METEOR_USER, else the first login user
```

`KEY=value` per line, value to the end of the line (no quoting), parsed and never sourced.
Unknown keys are ignored with a warning. Invalid values (host name, timezone, Wi-Fi
password length, SSH key format) are skipped with a warning; enrollment still runs.

- **Wi-Fi:** a NetworkManager connection `meteor-wifi` (mode 0600), or `wpa_supplicant.conf`
  with the derived key on images without NetworkManager. The country is set through
  `raspi-config` when present (it also unblocks Wi-Fi), otherwise `cfg80211`.
- **User:** like Raspberry Pi Imager. The image's first user (uid 1000) is renamed to
  `METEOR_USER` (home and group too), or the user is created with sudo. The password is set
  from `METEOR_USER_PASSWORD_HASH` (`$6$`, `$5$` or `$y$` crypt only; plain text is refused).
- **SSH:** `METEOR_SSH_PASSWORD_AUTH` writes `/etc/ssh/sshd_config.d/10-meteor.conf`.
  `METEOR_SSH_ENABLED` turns `ssh` on or off. The SSH key is appended to the user's
  `~/.ssh/authorized_keys` and turns `ssh` on unless `METEOR_SSH_ENABLED=false`.
- v1 files (`METEOR_DOMAIN` and `METEOR_API_KEY` only) still work.

## Build an OS image (golden image)

To publish an image with Docker, packages and meteorcli already installed:

1. Write a plain OS (for example Raspberry Pi OS Lite Bookworm), boot it and install what every
   device needs (Docker, packages, `meteorcli`). Do not enroll it, or prepare will remove the
   enrollment.
2. Prepare the card on the device. It powers off when done:
   ```sh
   curl -fsSLO https://github.com/meteor-edge/meteor-cloud/releases/latest/download/meteorcli-prepare-image.sh
   sudo bash meteorcli-prepare-image.sh          # asks you to type "prepare"; --yes for scripts
   ```
   It installs first boot and `.meteor-settings` (`install-meteorcli-firstboot.sh --image`), and
   removes this device's identity: `/etc/meteorcli` contents, `/etc/meteorcli-firstboot.env`,
   Wi-Fi profiles (`--keep-wifi` keeps them), cloud-init state (cloud-init is disabled),
   settings files on the boot partition, SSH host keys, `/etc/machine-id`, shell history and
   logs. `--zero-free-space` makes the backup compress better; `--no-poweroff` stays on.
3. Back up the card with Meteor Imager (**Back up a card**, or
   `meteor-imager backup --device /dev/sdX --out golden.img.gz`), upload it as an `os_image`
   artifact and publish it ([artifacts](../../../../../docs/fleet/artifacts.md#meteor-imager-public-os-images)).

On each new card's first boot, `meteorcli-settings` grows the root partition to the card (once),
creates new SSH host keys, applies `.meteorenv` and first boot sends one token request.

## Request limits and retries

First boot waits a random 0–30 s before the first request (`METEOR_START_JITTER_SECONDS`), so
a batch powered on together is spread out. A failed request is retried after 10 s, doubling
up to 5 min, plus jitter (`METEOR_RETRY_BASE_SECONDS`, `METEOR_RETRY_MAX_SECONDS`), and never
sooner than the server's `Retry-After`. A wrong, revoked or expired key stops first boot at
once (no retries until the next boot or `systemctl restart meteorcli-firstboot`). Only one
enrollment run is active at a time (`/run/meteorcli-firstboot.lock`).

## What first boot does

1. Waits for the network, installs latest `meteorcli` (`--with-service`) if missing.
2. `meteorcli config` + `meteorcli request-token --wait 0` (submit and return).
3. Starts `meteorcli-firstboot.service`, which loops `meteorcli claim --wait 300`
   until `/etc/meteorcli/device-token` exists.
4. `systemctl enable --now meteorcli`.
5. Disables and deletes `meteorcli-firstboot` (unit, script, `/etc/meteorcli-firstboot.env`).

Approve the pending row in the console (**Fleet → API keys** or **Devices**). The
device keeps polling in the background; it does not need a reboot after approve.

If `METEOR_DOMAIN` or `METEOR_API_KEY` is missing from the environment, `.meteorenv`
(including the boot partition), and `/etc/meteorcli-firstboot.env`, the script
prompts in this SSH/terminal session (not the HDMI console).

## Copy files by hand

```bash
sudo install -m 0755 meteorcli-firstboot.sh /usr/local/sbin/meteorcli-firstboot.sh
sudo install -m 0644 meteorcli-firstboot.service /etc/systemd/system/meteorcli-firstboot.service
sudo install -m 0755 meteorcli-settings.sh /usr/local/sbin/meteorcli-settings.sh
sudo install -m 0644 meteorcli-settings.service /etc/systemd/system/meteorcli-settings.service
sudo systemctl enable meteorcli-settings.service
sudo install -m 0600 meteorcli-firstboot.env.example /etc/meteorcli-firstboot.env
sudo editor /etc/meteorcli-firstboot.env
sudo systemctl enable --now meteorcli-firstboot.service
```

## After enroll

First-boot is gone. Only the agent remains:

```bash
sudo journalctl -u meteorcli -f
sudo meteorcli status
sudo systemctl is-enabled meteorcli
```
