# Simulated device: the real meteorcli agent with METEORCLI_PLATFORM=simulated.
# Build context: src/device-plane. Run it with `make sim-device` (docs/fleet/simulator.md).
# Development and test only; not part of a customer installation.
FROM python:3.13-slim

WORKDIR /opt/meteorcli
COPY agent ./agent
COPY runtime ./runtime
RUN pip install --no-cache-dir ./agent
COPY --chmod=0755 simulator/entrypoint.sh /usr/local/bin/meteorcli-sim

ENV METEORCLI_PLATFORM=simulated \
    METEORCLI_DEVICE_PROFILE=rpi5 \
    METEORCLI_CONFIG_DIR=/var/lib/meteorcli \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/meteorcli"]
ENTRYPOINT ["/usr/local/bin/meteorcli-sim"]
