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

# Option D: empty image + boot partition (Pi Imager / any flasher).
# After writing the OS image, copy .meteorenv onto the drive that shows
# as "boot" / "bootfs", then let first boot download and run this installer:
#   /boot/firmware/.meteorenv   (Bookworm+)
#   /boot/.meteorenv            (older images)
```

Empty-image first boot (stock Raspberry Pi OS, no meteorcli yet). Put
`.meteorenv` on the boot volume, then use Pi Imager’s first-boot script
or `firstrun.sh` to download the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh | sudo bash -s -- --now
```

The installer does not need the domain/key in the environment; first-boot
reads them from the boot partition. It copies the script and unit, submits
the pending request, and polls until approve. Then `meteorcli.service` stays
enabled and first-boot is deleted. Reboot is not required after approve.

`METEOR_DOMAIN` is the control-plane host (`cloud.example.com` or
`192.168.0.118:8000` for laptop `make dev`).

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
