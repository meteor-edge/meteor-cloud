# Test-only image: Debian 12 with systemd as PID 1, the meteorcli units and test
# users. Built and run by run.sh; never shipped.
FROM debian:12.8-slim
ENV container=docker DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty.target console-getty.service systemd-logind.service \
      systemd-firstboot.service systemd-remount-fs.service

COPY meteorcli holdconn fakeserver /usr/local/bin/
COPY packaging/meteorcli.service packaging/meteorcli-helper.service packaging/meteorcli-helper.socket /etc/systemd/system/
COPY packaging/sysusers.d/meteorcli.conf /usr/lib/sysusers.d/meteorcli.conf
COPY packaging/tmpfiles.d/meteorcli.conf /usr/lib/tmpfiles.d/meteorcli.conf

# alice manages (an admin is in both groups: agent.sock belongs to meteorcli),
# carol reads, bob is in neither group, erin is only in meteorcli-admin, and dave
# may open both sockets (meteor + meteorcli groups) but is not the meteor user.
RUN systemd-sysusers \
 && useradd -m -G meteorcli,meteorcli-admin alice \
 && useradd -m -G meteorcli-admin erin \
 && useradd -m -G meteorcli carol \
 && useradd -m bob \
 && useradd -m -G meteor,meteorcli dave \
 && systemd-tmpfiles --create /usr/lib/tmpfiles.d/meteorcli.conf \
 && systemctl enable meteorcli.service meteorcli-helper.socket

# A configured, enrolled device (fake values; no server is contacted).
RUN printf '{"version":1,"domain":"meteor.example"}\n' > /etc/meteorcli/config.json \
 && chown root:meteor /etc/meteorcli/config.json && chmod 0640 /etc/meteorcli/config.json \
 && printf '{"version":1,"device_id":"dev-test-1","organization_id":"org-1","name":"edge-test"}\n' \
      > /var/lib/meteorcli/identity/device.json \
 && printf 'dev_faketokenfaketokenfaketoken\n' > /var/lib/meteorcli/identity/device-token \
 && chown -R meteor:meteor /var/lib/meteorcli/identity \
 && chmod 0600 /var/lib/meteorcli/identity/*

# A fake control plane for the enrollment checks (test only), as a unit so it
# survives the container restart that stands in for a reboot.
RUN mkdir -p /var/lib/fakeserver \
 && printf '[Unit]\nDescription=fake control plane (test)\nBefore=meteorcli.service\n[Service]\nExecStart=/usr/local/bin/fakeserver\n[Install]\nWantedBy=multi-user.target\n' \
      > /etc/systemd/system/fakeserver.service \
 && systemctl enable fakeserver.service

HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD ["systemctl", "is-active", "--quiet", "meteorcli.service"]
STOPSIGNAL SIGRTMIN+3
# systemd must start as root (PID 1); the agent itself runs as meteor (User= in
# meteorcli.service), which is what this image tests.
# trivy:ignore:DS-0002
USER root
CMD ["/sbin/init"]
