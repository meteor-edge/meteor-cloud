import { describe, expect, it } from "vitest";

import {
  checkEndpointPort,
  dockerHostPorts,
  existingEndpointFor,
  suggestedEndpointPort,
} from "@/components/deployments/ports";

const docker = (ports: string[]) => ({ kind: "docker" as const, spec: { container_name: "web", ports } });

describe("dockerHostPorts", () => {
  it("reads the host side of each mapping", () => {
    expect(dockerHostPorts({ ports: ["8080:80", "127.0.0.1:9000:9000", "53:53/udp", "80"] })).toEqual([
      { port: 8080, protocol: "tcp", loopbackOnly: false },
      { port: 9000, protocol: "tcp", loopbackOnly: true },
      { port: 53, protocol: "udp", loopbackOnly: false },
    ]);
  });
});

describe("endpoint port checks", () => {
  it("suggests the first reachable tcp host port", () => {
    expect(suggestedEndpointPort(docker(["127.0.0.1:9000:9000", "8080:80"]))).toBe("8080");
    expect(suggestedEndpointPort({ kind: "systemd", spec: {} })).toBe("");
  });

  it("allows the app's own endpoint port and rejects another app's", () => {
    const endpoints = [
      { workload_key: "web", port: 8080, enabled: true, hostname: "web.example.com" },
      { workload_key: "shop", port: 9000, enabled: true, hostname: "shop.example.com" },
    ] as never;
    expect(checkEndpointPort(docker(["8080:80", "9000:90"]), 8080, endpoints)).toBeNull();
    expect(checkEndpointPort(docker(["8080:80", "9000:90"]), 9000, endpoints)?.level).toBe("error");
  });
});

describe("existingEndpointFor", () => {
  it("prefers an enabled row, then the hostname", () => {
    const endpoints = [
      { workload_key: "web", port: 8080, enabled: false, hostname: "a.example.com" },
      { workload_key: "web", port: 8080, enabled: true, hostname: "c.example.com" },
      { workload_key: "web", port: 8080, enabled: true, hostname: "b.example.com" },
    ] as never;
    expect(existingEndpointFor(docker(["8080:80"]), endpoints)?.hostname).toBe("b.example.com");
  });
});
