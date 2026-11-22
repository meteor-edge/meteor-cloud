import type { Device } from "@/api/fleet";

/** One agent module's health, as the Go meteorcli reports it in capabilities.modules. */
export type ModuleHealth = {
  state: "running" | "starting" | "waiting" | "failed" | "parked" | "stopped";
  restarts?: number;
  last_error?: string;
  since?: string;
};

/** What the device's agent said it can do on its last heartbeat (null: it never said). */
export type AgentCapabilities = {
  actions: string[] | null;
  deployKinds: string[] | null;
  modules: Record<string, ModuleHealth> | null;
  reportedAt: string | null;
};

function stringList(value: unknown): string[] | null {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : null;
}

export function agentCapabilities(device: Pick<Device, "metadata"> | undefined): AgentCapabilities {
  const caps = device?.metadata?.capabilities;
  const record =
    caps && typeof caps === "object" && !Array.isArray(caps)
      ? (caps as Record<string, unknown>)
      : {};
  const modules = record.modules;
  const reportedAt = device?.metadata?.capabilities_reported_at;
  return {
    actions: stringList(record.actions),
    deployKinds: stringList(record.deploy_kinds),
    modules:
      modules && typeof modules === "object" && !Array.isArray(modules)
        ? (modules as Record<string, ModuleHealth>)
        : null,
    reportedAt: typeof reportedAt === "string" ? reportedAt : null,
  };
}

/** True unless the agent listed its action kinds and this one is not among them. */
export function supportsAction(caps: AgentCapabilities, kind: string): boolean {
  return caps.actions === null || caps.actions.includes(kind);
}

/** True unless the agent listed its deploy kinds and this one is not among them. */
export function supportsDeployKind(caps: AgentCapabilities, kind: string): boolean {
  return caps.deployKinds === null || caps.deployKinds.includes(kind);
}
