import type { ApplicationEndpoint, Release } from "@/api/fleet/types";

/** Same per-device workload key the control plane derives from a release spec. */
export function workloadKey(release: Pick<Release, "kind" | "spec">): string {
  const spec = release.spec ?? {};
  if (release.kind === "systemd") return String(spec.unit_name ?? "");
  if (release.kind === "docker") return String(spec.container_name ?? "");
  return String(spec.project_name ?? "");
}

export type HostPort = { port: number; loopbackOnly: boolean; protocol: string };

/** Host ports from Docker's `ports` short syntax (`8080:80`, `127.0.0.1:8080:80`, `53:53/udp`). */
export function dockerHostPorts(spec: Record<string, unknown> | null | undefined): HostPort[] {
  const raw = Array.isArray(spec?.ports) ? (spec.ports as unknown[]) : [];
  const result: HostPort[] = [];
  for (const item of raw) {
    const [mapping, protocol = "tcp"] = String(item).trim().split("/", 2);
    let hostIp: string | null = null;
    let parts: string[];
    if (mapping.startsWith("[")) {
      const close = mapping.indexOf("]");
      if (close === -1) continue;
      hostIp = mapping.slice(1, close);
      parts = mapping.slice(close + 1).replace(/^:/, "").split(":");
    } else {
      parts = mapping.split(":");
      if (parts.length === 3) hostIp = parts.shift() || null;
    }
    if (parts.length !== 2) continue;
    const port = Number(parts[0]);
    if (!Number.isInteger(port) || port < 1 || port > 65535) continue;
    result.push({
      port,
      protocol: protocol.toLowerCase(),
      loopbackOnly: hostIp === "127.0.0.1" || hostIp === "::1" || hostIp === "localhost",
    });
  }
  return result;
}

/** Port to prefill for "Publish on the internet": the first reachable TCP host port of a docker release. */
export function suggestedEndpointPort(release: Pick<Release, "kind" | "spec"> | undefined): string {
  if (!release || release.kind !== "docker") return "";
  const match = dockerHostPorts(release.spec).find((p) => p.protocol === "tcp" && !p.loopbackOnly);
  return match ? String(match.port) : "";
}

export type PortCheck = { level: "error" | "info"; message: string } | null;

/**
 * Checks the Publish port before submitting. The control plane enforces the same rules
 * (plus docker port clashes between workloads); this just says it earlier.
 */
export function checkEndpointPort(
  release: Pick<Release, "kind" | "spec"> | undefined,
  port: number,
  endpoints: ApplicationEndpoint[],
): PortCheck {
  if (!release || !Number.isInteger(port) || port < 1 || port > 65535) return null;
  const key = workloadKey(release);
  if (release.kind === "docker") {
    const tcp = dockerHostPorts(release.spec).filter((p) => p.protocol === "tcp");
    const match = tcp.find((p) => p.port === port);
    if (tcp.length > 0 && !match) {
      return {
        level: "error",
        message: `This release publishes host port ${tcp.map((p) => p.port).join(", ")}; use that, not the container port.`,
      };
    }
    if (match?.loopbackOnly) {
      return {
        level: "error",
        message: `Port ${port} is bound to localhost only, so the public address cannot reach it.`,
      };
    }
  }
  const clash = endpoints.find(
    (e) => e.enabled && e.port === port && (!key || e.workload_key !== key),
  );
  if (clash) {
    return {
      level: "error",
      message: `Port ${port} is already published on this device for ${clash.workload_key ?? "another app"} (${clash.hostname}).`,
    };
  }
  return null;
}

/** The app's current public address on this device, which a redeploy keeps. */
export function existingEndpointFor(
  release: Pick<Release, "kind" | "spec"> | undefined,
  endpoints: ApplicationEndpoint[],
): ApplicationEndpoint | undefined {
  if (!release) return undefined;
  const key = workloadKey(release);
  return key ? endpoints.find((e) => e.workload_key === key) : undefined;
}
