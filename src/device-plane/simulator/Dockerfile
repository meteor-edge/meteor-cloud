# Simulated device: the real meteorcli agent with METEORCLI_PLATFORM=simulated.
# Build context: src/device-plane. Run it with `make sim-device` (docs/fleet/simulator.md).
# Development and test only; not part of a customer installation.
FROM python:3.13-slim

WORKDIR /opt/meteorcli
COPY agent ./agent
COPY runtime ./runtime
RUN pip install --no-cache-dir ./agent
COPY --chmod=0755 simulator/entrypoint.sh /usr/local/bin/meteorcli-sim
# A simulated device needs no root: its state lives in METEORCLI_CONFIG_DIR and
# host actions are simulated (docs/fleet/simulator.md).
RUN useradd --system --uid 10001 --create-home --home-dir /home/meteor meteor \
    && install -d -o meteor -g meteor -m 0700 /var/lib/meteorcli

ENV METEORCLI_PLATFORM=simulated \
    METEORCLI_DEVICE_PROFILE=rpi5 \
    METEORCLI_CONFIG_DIR=/var/lib/meteorcli \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/meteorcli"]
USER meteor
# Healthy once the simulated device holds its credential (registration done).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD test -s /var/lib/meteorcli/device-token
ENTRYPOINT ["/usr/local/bin/meteorcli-sim"]
