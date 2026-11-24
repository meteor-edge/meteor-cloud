# MeteorCloud bundled MQTT broker: Eclipse Mosquitto + the meteorcloud_auth plugin.
# Build context: src/data-plane/broker/mosquitto
#
# Mosquitto comes from Debian (EPL-2.0 / EDL-1.0, security updates from Debian).
# The plugin is our own Go code with no third-party modules.
FROM golang:1.24-trixie AS plugin

RUN apt-get update \
    && apt-get install -y --no-install-recommends mosquitto-dev libmosquitto-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY plugin/ ./
RUN go vet ./... && go test ./authz
RUN CGO_ENABLED=1 go build -buildmode=c-shared -trimpath -ldflags="-s -w" \
    -o /out/meteorcloud_auth.so .

FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends mosquitto ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o 1000 -g 1000 /mosquitto/config /mosquitto/data /mosquitto/certs

COPY --from=plugin /out/meteorcloud_auth.so /usr/lib/mosquitto/meteorcloud_auth.so
COPY mosquitto.conf /mosquitto/config/mosquitto.conf

# Same UID the certificate tooling already uses for the broker key.
USER 1000:1000
EXPOSE 8883
VOLUME ["/mosquitto/data"]

HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
    CMD bash -c '</dev/tcp/127.0.0.1/8883' || exit 1

CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
