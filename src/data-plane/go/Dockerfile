# Build context: src/data-plane/go
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/dataplane ./cmd/dataplane

FROM alpine:3.21
RUN adduser -D -H -u 10001 dataplane
COPY --from=build /out/dataplane /usr/local/bin/dataplane
USER 10001
# 8082: application API (public). 8092: metrics, readiness and the internal API (private).
EXPOSE 8082 8092
HEALTHCHECK --interval=10s --timeout=5s --retries=10 CMD ["dataplane", "healthcheck"]
ENTRYPOINT ["dataplane"]
