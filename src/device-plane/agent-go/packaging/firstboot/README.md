# First boot and OS images

Scripts shipped with every meteorcli release (`agent-v*`, see [scripts/dist.sh](../../scripts/dist.sh)):

| File | What it does |
| --- | --- |
| `meteorcli-settings.sh` + `meteorcli-settings.service` | Early in boot: applies a card's `.meteorenv` (Wi-Fi, host name, timezone, user, SSH) and hands the enrollment values to meteorcli |
| `install-meteorcli-firstboot.sh` | Installs meteorcli (when missing) and the settings service, and marks the card for Meteor Imager (`.meteor-settings`) |
| `meteorcli-prepare-image.sh` | Turns a device into an OS image: the identity goes, meteorcli and the settings service stay |
| `cloud-init-user-data.yaml` | A cloud-init `runcmd` that runs `install-meteorcli-firstboot.sh` on a stock image |

meteorcli does the enrollment itself. `meteorcli.service` applies the enrollment values as root
before the agent starts (`meteorcli config meteorenv`), from `/etc/meteorcli/meteorenv` or
from a `meteorenv` file on `/boot/firmware` or `/boot`. The agent then sends the request and
keeps polling for the approval in the background, across reboots. An expired request is sent
again with the saved key, and a refused key stops the requests. Details:
[Enrollment](../../../../../docs/meteorcli-go.md#enrollment). Follow it with
`journalctl -u meteorcli -f` and approve the device in the console.

This is Path B (enrollment API key). The key does not join the fleet by itself.

## One command (a live device)

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli-firstboot.sh \
  | sudo METEOR_DOMAIN=192.168.0.118:8000 METEOR_API_KEY=key_... METEOR_NAME=pi-lab-01 bash
```

Without the variables, the device enrolls on the next boot from a `.meteorenv` on its boot
partition. On a single device you can also run the plain installer:
`install-meteorcli.sh --domain … --api-key …` ([meteorcli](../../../../../docs/meteorcli.md#install)).

## Empty Raspberry Pi OS image (cloud-init, no SSH)

Meteor Imager does this for you (`meteor-firstrun.sh`). By hand, on a current Raspberry Pi OS
(the bootfs has `user-data`):

1. Flash the OS. Copy `.meteorenv` onto the **bootfs** drive:

   ```text
   METEOR_DOMAIN=192.168.0.118:8000
   METEOR_API_KEY=key_...
   ```

   Use an address the **Pi** can reach (the laptop's LAN IP, not `localhost`).
2. In `user-data` on the same drive, keep the first line `#cloud-config` and append the
   `packages:` and `runcmd:` block from `cloud-init-user-data.yaml`.
3. Boot the Pi, wait for the network, and approve the pending device in the console.

On the first boot, cloud-init runs `install-meteorcli-firstboot.sh`, which installs the agent
without starting it, enables `meteorcli-settings.service`, runs that service once so Wi-Fi,
user and SSH settings apply and enrollment moves to `/etc/meteorcli/meteorenv`, then starts
the agent.

`METEOR_DOMAIN` is the control-plane host (`cloud.example.com`, or `192.168.0.118:8000` for a
laptop `make dev`).

## Settings on the card (`.meteorenv` v2, Meteor Imager)

`meteorcli-settings.service` runs early in boot, before the network starts. It reads
`.meteorenv` from the card's config partition, applies the settings, moves the enrollment
values to `/etc/meteorcli/meteorenv` (root only) and **deletes `.meteorenv` from the
card**. Without `.meteorenv` it does nothing. Log: `/var/log/meteorcli-settings.log`.
On an enrolled device the enrollment values are dropped; the other settings still apply.

The file may also be named `meteorenv.txt`, `meteorenv` or `.meteorenv.txt` (the console's
download is `meteorenv.txt`, since Windows and macOS make dot-files awkward); Windows line
ends and a byte-order mark are accepted. See
[Device settings file](../../../../../docs/fleet/device-settings-file.md).

Config partition, first match: a FAT partition labelled `METEORCFG` (for Jetson, NUC and
other device types), `/boot/firmware` (Raspberry Pi OS Bookworm+), `/boot`, `/boot/efi`.
The installer writes `.meteor-settings` there, which tells Meteor Imager to write only
`.meteorenv` and leave cloud-init alone.

```text
METEOR_ENV_VERSION=2
METEOR_DOMAIN=fleet.example.com:8443
METEOR_API_KEY=key_...
# Optional from here on.
# Only when the scheme matters:
METEOR_API_BASE=https://fleet.example.com:8443
# Name in MeteorCloud:
METEOR_NAME=pi-lab-01
METEOR_HOSTNAME=pi-lab-01
METEOR_TIMEZONE=Europe/Berlin
# Wi-Fi: SSID with PSK and COUNTRY.
METEOR_WIFI_SSID=My Network
METEOR_WIFI_PSK=wifi-password
METEOR_WIFI_COUNTRY=DE
METEOR_WIFI_HIDDEN=false
# Login user; the password only as a crypt hash, never plain text.
METEOR_USER=ops
METEOR_USER_PASSWORD_HASH=$6$salt$hash
# SSH on or off; PASSWORD_AUTH=false allows keys only.
METEOR_SSH_ENABLED=true
METEOR_SSH_PASSWORD_AUTH=false
# Key user: default METEOR_USER, else the first login user.
METEOR_SSH_AUTHORIZED_KEY=ssh-ed25519 AAAA... me@laptop
METEOR_SSH_USER=pi
```

`KEY=value` per line, value to the end of the line (no quoting, so no `# comments` after a
value; put them on their own line), parsed and never sourced.
Unknown keys are ignored with a warning. Invalid values (host name, timezone, Wi-Fi
password length, SSH key format) are skipped with a warning; enrollment still runs.
If applying a setting fails (for example a write error), `.meteorenv` stays on the card
and the next boot tries again; the log says which setting failed.

- **Wi-Fi:** a NetworkManager connection `meteor-wifi` (mode 0600) when NetworkManager is
  enabled, otherwise `wpa_supplicant.conf` with the derived key. The country is set through
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
   It installs meteorcli, `meteorcli-settings.service` and `.meteor-settings` when missing
   (`install-meteorcli-firstboot.sh --image`), and removes the old Python
   `meteorcli-firstboot.service` if the image has it. It removes this device's identity:
   - meteorcli's enrollment, keys and state (`/var/lib/meteorcli/{identity,state,artifacts,app-credentials}`)
     and the helper's WireGuard config;
   - the server settings in `/etc/meteorcli`. `ssh_user` and the registry logins in
     `/etc/meteorcli/docker` stay;
   - Wi-Fi profiles (`--keep-wifi` keeps them) and cloud-init state (cloud-init is disabled);
   - settings files on the boot partition;
   - SSH host keys, `/etc/machine-id`, shell history and logs.

   The agent stays enabled. `--zero-free-space` makes the backup compress better;
   `--no-poweroff` stays on.
3. Back up the card with Meteor Imager (**Back up a card**, or
   `meteor-imager backup --device /dev/sdX --out golden.img.gz`), upload it as an `os_image`
   artifact and publish it ([artifacts](../../../../../docs/fleet/artifacts.md#meteor-imager-public-os-images)).

On Linux, Meteor Imager extends the root partition to the card while writing and grows the
filesystem when `e2fsprogs` is installed. On each new card's first boot, `meteorcli-settings`
still grows the filesystem once (the partition may already fill the card), creates new SSH host
keys and applies `.meteorenv`; the agent then sends one token request.

## Copy files by hand

```bash
sudo install -m 0755 meteorcli-settings.sh /usr/local/sbin/meteorcli-settings.sh
sudo install -m 0644 meteorcli-settings.service /etc/systemd/system/meteorcli-settings.service
sudo systemctl enable meteorcli-settings.service
```

## Tests

`tests/firstboot` (pytest, in CI as *First-boot scripts*) runs `meteorcli-settings.sh` and
`meteorcli-prepare-image.sh` against a fake root. `make agent-go-install-test` covers the whole
flow under systemd in a container: install `--image`, a card's `.meteorenv`, and the agent's
request.
