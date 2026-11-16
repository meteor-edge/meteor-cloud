# Device simulator

Test MeteorCloud against a Raspberry Pi, NUC or Jetson without the hardware. The real
`meteorcli` agent runs; only the hardware facts are simulated, so the simulator does not
drift from what the agent really does.

This is phase 1 of [#48](https://github.com/meteor-edge/meteor-cloud/issues/48): one
simulated device, in a container or a VM. A fleet runner, arm64 in CI and fault injection
come later.

## What is simulated

Turn it on with `METEORCLI_PLATFORM=simulated`. It is never chosen automatically.

| Port | Simulated device |
|------|------------------|
| Inventory | `hardware_model`, `cpu_model`, `cpu_cores`, `memory_mb` and `architecture` come from the profile. Each instance gets a stable serial number (`SIM-…`) and MAC address (`02:…`, locally administered, so it never matches a real vendor). `hostname`, `local_ip` and OS fields stay real. |
| Metrics | CPU, memory and disk load are real, sized to the profile's memory and disk. `temperature_c` follows CPU load with some noise. Raspberry Pi profiles add `throttled` (the `vcgencmd get_throttled` bits). The Jetson profile adds `gpu_percent`, `gpu_temperature_c` and `power_mode`. |
| Capabilities | Real probes, with the profile's overrides on top. `industrial-gateway` turns Docker off to test deploy gating. |
| Host actions | **Restart** stops the agent for the profile's boot delay and then starts it again, so the device goes offline and reconnects. **Shut down** stops the agent. **Add user** and **change password** are recorded only, so the host does not change. |

A simulated device always sends `simulated: true`. The control plane stores it as
`metadata.simulated`, and the console shows a **Simulated** badge in the device list and on
the device page, so a simulated device is never mistaken for a real one. The key belongs to
the agent: a real device's next heartbeat removes it.

## Profiles

Profiles are JSON files in `src/device-plane/agent/edge_agent/platform/profiles/`. A new
device type is a file, not code.

| Profile | Board model it reports | Arch | Extras |
|---------|------------------------|------|--------|
| `rpi4` | Raspberry Pi 4 Model B Rev 1.5 | aarch64 | throttling flags, 32 GB SD card |
| `rpi5` (default) | Raspberry Pi 5 Model B Rev 1.0 | aarch64 | throttling flags, 64 GB SD card |
| `nuc` | NUC13ANHi5 | x86_64 | 16 threads, 512 GB NVMe |
| `jetson-orin-nano` | NVIDIA Jetson Orin Nano Developer Kit | aarch64 | GPU load, power mode |
| `industrial-gateway` | Industrial Gateway IG-200 | x86_64 | no Docker or Compose |

The board models are the strings real boards report, so default names and device type
matching work as on real hardware. A catalog [device type](device-types.md) named
`Raspberry Pi 5` matches the `rpi5` profile, and one with model `NUC13ANHi5` matches `nuc`.

Your own profile: copy one of these files, change it, and set
`METEORCLI_DEVICE_PROFILE=/path/to/my-board.json`. The profile needs an `inventory` object
with `hardware_model`, `cpu_model`, `cpu_cores`, `memory_mb` and `architecture`. The other
keys are optional:

| Key | Meaning |
|-----|---------|
| `disk_gb` | Disk size used for `disk_total_bytes` |
| `thermal` | `idle_c`, `load_c`, `throttle_c`, `noise_c` for the simulated temperature |
| `extras` | `raspberry_pi`, `jetson` or `null` |
| `capabilities` | Overrides for the probed capabilities, e.g. `{"docker": false}` |
| `boot_delay_seconds` | How long a restart keeps the device offline |

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `METEORCLI_PLATFORM` | `linux` | `simulated` turns the simulator on |
| `METEORCLI_DEVICE_PROFILE` | `rpi5` | Profile name or path to a profile file |
| `METEORCLI_SIM_ID` | hostname | What makes this device distinct. Same value, same serial and MAC. Set it when several simulated devices share a hostname. |
| `METEORCLI_SIM_REBOOT_COMMAND` | none | Run this on restart instead of restarting the agent process (e.g. restart the VM) |
| `METEORCLI_SIM_SHUTDOWN_COMMAND` | none | Run this on shut-down instead of stopping the agent |
| `METEORCLI_SIM_USERS` | `record` | `host` applies user changes to the real host through `meteorcli-action` |

## Container (fast loop)

Start the local stack with `make dev`, create a registration token in the console
(**Devices → Add device**), then run:

```bash
make sim-device PROFILE=rpi5 TOKEN=<registration token>
# or let it request a token with an enrollment API key, then approve it in the console:
make sim-device PROFILE=jetson-orin-nano API_KEY=<enrollment API key>
```

The device appears in the console with the **Simulated** badge. Each profile runs as its own
container (`sim-<profile>`), with its credential kept in a volume of the same name, so it keeps
its identity across restarts. Use `SIM_NAME=sim-rpi5-2` for a second device with the same
profile, and `SIM_SERVER=` for another installation.

```bash
docker logs -f sim-rpi5
docker rm -f sim-rpi5 && docker volume rm sim-rpi5   # remove it (delete the device in the console too)
```

The container uses host networking so it reaches the API and the MQTT broker at `localhost`,
like an agent on your machine. On Docker Desktop, turn on host networking in its settings or
run the image with `-e METEORCLI_SERVER=http://host.docker.internal:8000`. The image has no
Docker inside, so docker and compose deployments report the runtime as missing. The
registration token is passed as an environment variable: use it for local stacks only.

## Local VM (first boot, real systemd)

Use a VM to test first boot and the real `meteorcli.service`, for example an arm64 Debian VM
with QEMU (`virt` machine) on Linux, or UTM on Apple Silicon.

1. Download a Debian 12 arm64 cloud image (`debian-12-genericcloud-arm64.qcow2`) from
   cloud.debian.org.
2. Make a cloud-init NoCloud seed that installs first boot and turns the simulator on:

   ```yaml
   #cloud-config
   packages: [curl, ca-certificates]
   write_files:
     - path: /etc/systemd/system/meteorcli.service.d/simulated.conf
       content: |
         [Service]
         Environment=METEORCLI_PLATFORM=simulated
         Environment=METEORCLI_DEVICE_PROFILE=rpi5
         Environment="METEORCLI_SIM_REBOOT_COMMAND=/usr/local/libexec/meteorcli-action reboot"
         Environment="METEORCLI_SIM_SHUTDOWN_COMMAND=/usr/local/libexec/meteorcli-action shutdown"
     - path: /etc/meteorcli-firstboot.env
       permissions: "0600"
       content: |
         METEOR_DOMAIN=<your machine's LAN IP>:8000
         METEOR_API_KEY=<enrollment API key>
   runcmd:
     - curl -fsSL https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh | bash -s -- --now
   ```

   Save it as `user-data`, add an empty `meta-data`, and build the seed with
   `cloud-localds seed.img user-data meta-data`. The settings file is described in
   [first boot](../../src/device-plane/agent/packaging/firstboot/README.md). Approve the
   token request in the console once the VM is up.
3. Boot it:

   ```bash
   qemu-system-aarch64 -machine virt -cpu max -m 2048 -smp 4 -nographic \
     -bios /usr/share/qemu-efi-aarch64/QEMU_EFI.fd \
     -drive if=virtio,file=debian-12-genericcloud-arm64.qcow2 \
     -drive if=virtio,format=raw,file=seed.img \
     -nic user,model=virtio-net-pci
   ```

The reboot and shut-down commands above make **Restart** and **Shut down** act on the VM
itself, because the VM has a real systemd. Without them only the agent process restarts.

## What it does not cover

Real hardware is still needed for Pi firmware and the bootloader, A/B OS updates, Wi-Fi
radios through NetworkManager, GPIO, cameras, the Jetson CUDA runtime and real inference
performance, and SD-card corruption on power loss. Keep one Pi 5, one Jetson and one NUC for
those checks.
