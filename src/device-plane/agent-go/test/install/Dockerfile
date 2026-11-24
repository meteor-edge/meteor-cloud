# Test-only image for test/install/run.sh: Debian 12 with systemd as PID 1,
# curl, a user "pi", and the fake control plane from test/systemd. The
# installer under test is copied in by run.sh; nothing of meteorcli is
# preinstalled. Never shipped.
FROM debian:12.8-slim
ENV container=docker DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus curl ca-certificates passwd \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty.target console-getty.service systemd-logind.service \
      systemd-firstboot.service systemd-remount-fs.service \
 && useradd -m pi

COPY fakeserver /usr/local/bin/
RUN mkdir -p /var/lib/fakeserver \
 && printf '[Unit]\nDescription=fake control plane (test)\n[Service]\nExecStart=/usr/local/bin/fakeserver\n[Install]\nWantedBy=multi-user.target\n' \
      > /etc/systemd/system/fakeserver.service \
 && systemctl enable fakeserver.service

HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD ["systemctl", "is-active", "--quiet", "fakeserver.service"]
STOPSIGNAL SIGRTMIN+3
# systemd must start as root (PID 1).
# trivy:ignore:DS-0002
USER root
CMD ["/sbin/init"]
