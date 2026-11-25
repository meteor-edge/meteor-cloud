# meteorcli

The device agent. One static Go binary runs on managed Linux devices: it enrolls the device,
sends heartbeats, keeps an MQTT session, deploys software, runs console actions and updates
itself. Source: [`src/device-plane/agent-go/`](../src/device-plane/agent-go/). Internals (file
layout, socket policy, root helper, modules): [meteorcli internals](meteorcli-go.md).

## Install

On the device (Linux with systemd; arm64, 32-bit ARM from ARMv6 (Raspberry Pi Zero and 1), or
amd64). It does **not** clone this repository:

```bash
# Enroll right away with a registration token
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh \
  | sudo bash -s -- --domain meteor.example.com --token reg_XXXX --name lobby-cam
# Or by approval: the agent sends a request by itself; approve it in the console
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh \
  | sudo bash -s -- --domain meteor.example.com --api-key key_XXXX --name lobby-cam
```

The installer:

- picks the binary for the CPU (`uname -m`) and checks it, the systemd units, sysusers.d and
  tmpfiles.d files against `SHA256SUMS`;
- installs `/usr/local/bin/meteorcli`, `meteorcli.service`, `meteorcli-helper.socket` and
  `meteorcli-helper.service`, the `meteor` user and the `meteorcli` / `meteorcli-admin` groups,
  and `wireguard-tools` (`--no-wireguard` skips it);
- adds the user who ran it (`$SUDO_USER`, or `--user NAME`) to `meteorcli` and `meteorcli-admin`,
  and makes that user the account the console's browser terminal signs in as;
- runs `systemctl enable --now` for the agent, and `meteorcli register` when given a token.

Running it again upgrades in place. `--no-start` installs only. `install-rpi.sh` from the same
release is the same installer, but it refuses a board that is not a Raspberry Pi.

**No sudo after the install.** The agent runs as the unprivileged `meteor` user, and members of
`meteorcli-admin` talk to it over `/run/meteorcli/agent.sock`. Log in again after the install so
both the `meteorcli` and `meteorcli-admin` groups apply (a single `newgrp` adds only one group),
then:

```bash
meteorcli status
meteorcli request-token --name lobby-cam     # the agent waits for the approval, across reboots
meteorcli update
meteorcli logs systemd:meteorcli.service -f
```

Only `meteorcli config` (it writes `/etc/meteorcli`) needs root. See
[Who may do what](meteorcli-go.md#who-may-do-what).

**From the Python meteorcli (0.4.x and older).** The Python agent is gone, and the installer no
longer imports its identity. Wipe the device (or remove `/opt/meteorcli`, `/etc/meteorcli` and the
old `meteorcli.service`), install as above, and enroll it again. Its hardware is already a device,
so first click **Allow re-registration** on that device's page, or delete the device
([Device registration](fleet/device-registration.md)).

**Local build.** `make agent-dist` (or `make agent-go-dist`) writes the same release files into
`src/device-plane/agent-go/dist`. Run the installer as a file from that folder: it installs the
files next to it. To install over the network, serve the folder (`python3 -m http.server`) and
pass `--base-url http://laptop:8000`.

## Agent version and console update

Each heartbeat sends the installed version; the console shows it on the device **Overview**. On
**Manage device**, owners and admins click **Update meteorcli**. A `meteorcli-admin` on the device
runs `meteorcli update`. Both start `meteorcli updater` through the root helper.

## Update

The updater reads the newest release from `release_url`. The default is
`https://github.com/meteor-edge/meteor-cloud/releases/latest/download`, the newest `agent-v*`
release. The updater then:

- installs the release only when its manifest carries the MeteorCloud release signature and the
  binary matches the signed SHA-256;
- keeps the old binary as `/usr/local/bin/meteorcli.previous`;
- restarts the agent and waits up to 3 minutes for a heartbeat from the new version. Without one,
  it puts the previous binary back. The console then shows the update as failed, with the reason.

```bash
meteorcli update                                  # as a meteorcli-admin; returns at once
journalctl -u meteorcli-update -f                 # follow it
sudo meteorcli config --release-url https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge
sudo meteorcli config --release-url default       # back to the newest agent-v* release
```

The updater installs only a **higher** version. Edge builds are `0.0.0-edge.<run>`, which order
below a released version such as `0.5.0`, so setting `release_url` to `meteorcli-go-edge` alone
leaves a stable device on its current binary (`already_current`). To move a stable device onto
edge builds, re-run the edge installer, then point `release_url` at the edge folder so later
updates follow it:

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge/install-meteorcli.sh \
  | sudo bash
sudo meteorcli config --release-url https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge
```

`meteorcli-go-edge` is a prerelease built from every merge to master. Details:
[Updating](meteorcli-go.md#updating).

## First boot and OS images

A card written by Meteor Imager carries `.meteorenv` (Wi-Fi, host name, user, SSH, and the
enrollment domain, key and name). The console's device page can also download it as
`meteorenv.txt`. Two ways to have a device use it:

- **Stock Raspberry Pi OS image.** Meteor Imager adds `meteor-firstrun.sh`, which installs
  meteorcli on first boot and asks for approval.
- **Your own OS image.** Prepare a device once, then back it up with Meteor Imager:

  ```bash
  curl -fsSLO https://github.com/meteor-edge/meteor-cloud/releases/latest/download/meteorcli-prepare-image.sh
  sudo bash meteorcli-prepare-image.sh
  ```

  The script installs meteorcli and `meteorcli-settings.service` if they are missing (through
  `install-meteorcli-firstboot.sh --image`). It removes this device's identity, Wi-Fi networks,
  SSH host keys and logs, then powers off. On each card's first boot:
  - `meteorcli-settings.service` applies the settings early, before the network starts.
  - It moves the enrollment lines to `/etc/meteorcli/meteorenv` (root only) and deletes
    `.meteorenv` from the card.
  - `meteorcli.service` applies those lines (`meteorcli config meteorenv`, as root, before the
    agent starts).
  - The agent asks for approval by itself.

A device without the settings service still reads `meteorenv.txt` or `.meteorenv` from
`/boot/firmware` or `/boot` when the agent starts. Details and the file format:
[packaging/firstboot](../src/device-plane/agent-go/packaging/firstboot/README.md).

## Commands

| Command | Purpose | Who |
| --- | --- | --- |
| `config` | Server domain / API base, enrollment API key, `--enroll-name`, `--ssh-user`, `--release-url`; `--show` | root |
| `test` | Server reachable, API key valid | `meteorcli` |
| `register` | Enroll with a registration token | `meteorcli-admin` |
| `request-token` | Ask for approval; the agent keeps polling | `meteorcli-admin` |
| `claim` | Report or wait for the approval | `meteorcli-admin` |
| `status` | Configuration, enrollment and module health (never secrets) | `meteorcli` |
| `module restart NAME` | Restart (or unpark) one agent module | `meteorcli-admin` |
| `logs` | List log sources or print one (`meteorcli logs systemd:nginx.service -f --grep error`). See [Device logs](fleet/logs.md) | `meteorcli` |
| `mqtt-test` / `mqtt-listen` | MQTT diagnostics through the agent's session | `meteorcli-admin` / `meteorcli` |
| `update` | Update to the newest signed release | `meteorcli-admin` |
| `agent` (alias `run`) | The agent itself; `meteorcli.service` runs it | service |

`--domain` is the **API host**. IP addresses and localhost use HTTP; names use HTTPS unless
`--http` or `--api-base` is given. For a laptop `make dev`, include the port:
`--domain 192.168.0.118:8000`. Changing the server removes the old server's credentials, so the
device enrolls again on the new one.

## Publish a release (maintainers)

Releases are automatic. Every merge to `master` that changes the agent
(`src/device-plane/agent-go`, not its docs or tests) runs the **meteorcli release** workflow
(`agent-release.yml`). It picks the version: the newest `agent-v*` release with the patch number
plus one, or `src/device-plane/agent-go/VERSION` when that is higher. Bump `VERSION` in a PR only
for a new minor or major version (`0.6.0`; later merges release `0.6.1`, `0.6.2`, …). To release
`master` now, run the workflow by hand (**Run workflow**). The workflow:

1. runs the Go tests and the installer test under systemd;
2. builds `meteorcli-linux-{arm64,armv6,amd64}`;
3. signs the manifest with the `METEORCLI_RELEASE_SIGNING_KEY` secret (without it, the workflow
   fails instead of publishing an unsigned release);
4. creates the `agent-v<version>` tag and publishes it as the latest release, with the installers, the first-boot
   scripts and `SHA256SUMS`.

If the release already exists, nothing happens. A hand-pushed tag works too and must match
`VERSION`:

```bash
make agent-dist                 # optional local check
git tag agent-v0.5.1 && git push origin agent-v0.5.1
```

Every device on the default `release_url` updates to that release when it is ordered to update.

## Related

- [Getting started](getting-started.md)
- [Registration tokens](fleet/registration-tokens.md)
- [Device-initiated enrollment](fleet/device-request-enrollment.md)
- [Device actions](fleet/device-actions.md)
- [Heartbeat](fleet/heartbeat.md)
- [MQTT](fleet/mqtt.md)
- [meteorcli internals](meteorcli-go.md)
