import { describe, expect, it } from "vitest";

import { agentCapabilities, supportsAction, supportsDeployKind } from "./agentCapabilities";

describe("agentCapabilities", () => {
  it("keeps only module entries with a known state", () => {
    const caps = agentCapabilities({
      metadata: {
        capabilities: {
          modules: {
            deploy: { state: "parked", restarts: 5, last_error: "boom" },
            nully: null,
            listy: [],
            nostate: {},
            weird: { state: "exploded" },
            badstate: { state: ["running"] },
          },
        },
      },
    } as never);
    expect(caps.modules).toEqual({ deploy: { state: "parked", restarts: 5, last_error: "boom" } });
  });

  it("treats a missing list as everything supported", () => {
    const none = agentCapabilities({ metadata: {} } as never);
    expect(none.modules).toBeNull();
    expect(supportsAction(none, "restart_module")).toBe(true);
    const listed = agentCapabilities({
      metadata: { capabilities: { actions: ["reboot"], deploy_kinds: ["docker", 3] } },
    } as never);
    expect(supportsAction(listed, "restart_module")).toBe(false);
    expect(supportsDeployKind(listed, "docker")).toBe(true);
    expect(supportsDeployKind(listed, "docker_compose")).toBe(false);
  });
});
