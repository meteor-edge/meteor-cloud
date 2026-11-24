# NOTICE

MeteorCloud is licensed under the Apache License 2.0 ([LICENSE](LICENSE)).

MeteorCloud's container images and device images include third-party software under
their own licences. The full inventory, with the licence and production position of
each component, is in [docs/licensing.md](docs/licensing.md). Highlights:

- **Container images** are built on official `python`, `node`/`nginx`, `golang`/`alpine`
  and `debian` base images. Their operating-system packages (for example BusyBox, GNU
  libc, OpenSSL) keep their own licences, including GPL-2.0 for some userland tools.
  They are unmodified; their source is available from the respective distributions
  (Alpine `aports`, Debian source archives).
- **Eclipse Mosquitto** (EPL-2.0 or EDL-1.0) is installed unmodified from Debian in the
  `meteorcloud/mosquitto` image. The MeteorCloud authentication plugin is separate code.
- **psycopg** (LGPL-3.0) is used unmodified by the control plane.
- **Device images** made with Meteor Imager contain Raspberry Pi OS or Debian (Linux
  kernel and userland, mostly GPL), Docker Engine (Apache 2.0) and `meteorcli`
  (Apache 2.0). If you give such an image to someone else, you redistribute GPL
  software: provide, or offer in writing, the corresponding source (the matching
  Raspberry Pi OS / Debian source packages) together with this notice.

Optional components that MeteorCloud can start, but does not include by default
(Grafana, Loki and Alloy under AGPL-3.0; MinIO under AGPL-3.0; VerneMQ official
binaries under the VerneMQ EULA; EMQX 5.9+ under BSL 1.1), are described in
docs/licensing.md together with the conditions for using them.
