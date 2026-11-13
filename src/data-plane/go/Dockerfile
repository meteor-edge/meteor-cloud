# Build context: src/data-plane/go
# One image, several entrypoints: dataplane (application data API, the default),
# device-ingest, rule-processor and clickhouse-writer (device event path over
# NATS JetStream). Compose and Helm pick the binary with `entrypoint`.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN for c in dataplane device-ingest rule-processor clickhouse-writer; do \
      CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/$c ./cmd/$c || exit 1; \
    done

FROM alpine:3.21
RUN adduser -D -H -u 10001 dataplane
COPY --from=build /out/ /usr/local/bin/
USER 10001
# 8082: application API (public). 8092: metrics, readiness and the internal API (private).
# 8093-8095: metrics and readiness of device-ingest, rule-processor, clickhouse-writer (private).
EXPOSE 8082 8092
HEALTHCHECK --interval=10s --timeout=5s --retries=10 CMD ["dataplane", "healthcheck"]
ENTRYPOINT ["dataplane"]
