# Device settings file (set up a card without Meteor Imager)

The console can build the same settings file that
[Meteor Imager](https://github.com/meteor-edge/meteor-imager) writes to a card.
The dialog is split like Raspberry Pi Imager customisation first (hostname,
timezone, username/password with Generate, Wi-Fi SSID/password, SSH), then
**optional** MeteorCloud enrollment (domain and enrollment key). You download
one file, copy it to the card's boot partition, and the device applies it on
first boot.

Where to find it:

- **Devices → Set up a device card.** Only the settings file.
- **Artifacts** or a device type's page → **Download** on an OS image. The dialog
  offers the image and the settings file together, with the steps below. People who
  cannot add devices get the plain download.

## Steps

1. Download the OS image and write it to the card with any tool (Raspberry Pi Imager
   without customisation, balenaEtcher, `dd`).
2. Fill the form and download `meteorenv.txt`.
3. Put the card back in the computer. Copy `meteorenv.txt` to its boot partition: the
   small drive that appears, usually named `bootfs` (or `METEORCFG` on images that
   have that partition). Do not rename it and do not put it in a folder.
4. Eject the card, put it in the device and power it on. The device applies the
   settings, deletes the file from the card and sends its enrollment request. Approve
   it under **Devices → Pending**.

On a device that already has **meteorcli 0.3.2+** and `meteorcli.service` (for example
after `install-meteorcli.sh` on stock Raspberry Pi OS), the service itself reads
`METEOR_DOMAIN` / `METEOR_API_KEY` / `METEOR_NAME` from that file, saves them under
`/etc/meteorcli`, submits the enrollment request, and removes the file. Wi-Fi,
hostname, user, and SSH still need the first-boot **settings** unit (or apply them
another way).

## Same result as Meteor Imager

The file is `.meteorenv` version 2, key for key what the imager writes
(`METEOR_DOMAIN`, `METEOR_API_KEY`, `METEOR_WIFI_*`, `METEOR_HOSTNAME`,
`METEOR_TIMEZONE`, `METEOR_USER`, `METEOR_USER_PASSWORD_HASH`, `METEOR_SSH_*`), and
the device reads it with the same script (`meteorcli-settings.sh`). A test renders a
file through the console's route and runs that script on it.

Differences, all on the outside:

- **File name.** The download is `meteorenv.txt`, because Windows and macOS hide or
  refuse files that start with a dot. The first-boot script accepts `.meteorenv`,
  `meteorenv.txt`, `meteorenv` and `.meteorenv.txt`, and tolerates Windows line ends
  and a byte-order mark. Images built with meteorcli **before 0.3.0** only read
  `.meteorenv`: rename the file on the card for those.
- **Base image.** Full first-boot (Wi-Fi, user, SSH, hostname, plus enrollment) needs an
  image with meteorcli and the first-boot units
  ([first-boot image](../../src/device-plane/agent/packaging/firstboot/README.md)).
  Enrollment alone also works on stock OS once meteorcli **0.3.2+** is installed:
  `meteorcli.service` reads the enrollment keys from the boot file. The imager still
  handles cloud-init on stock images when you use Meteor Imager instead of this file.
- **Login password.** It is turned into a SHA-512 crypt hash on the server and only the
  hash is in the file, as with the imager. The Wi-Fi password and the enrollment key are
  in the file in plain text (the device needs them), so treat the file like a password
  and delete it from your downloads after copying.

## API

`POST /api/v1/organizations/{org}/device-config` with `device.create`. Body: any of
`enrollment { domain, api_key, device_name }`, `hostname`, `timezone`,
`wifi { ssid, psk, country, hidden }`, `user { name, password }`,
`ssh { enabled, password_authentication, authorized_key, user }`. Response:
`{ "file_name": "meteorenv.txt", "content": "…" }`. The server stores nothing and
does not log the body.

The console can create an enrollment key for the file (owners and admins), or you
use an existing one: pick it from the list of active keys (name and prefix, with
`enrollment_key.read`) and paste it. MeteorCloud keeps no copy of a key after it
is shown, so the key itself always has to be pasted; the console checks it is the
one picked and refuses keys that are revoked or expired.

To change Wi-Fi on a device that is already enrolled, see [Wi-Fi](wifi.md).
