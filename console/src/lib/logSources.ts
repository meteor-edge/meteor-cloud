import type { LogSource } from "@/api/fleet/types";

export function sourceKey(source: Pick<LogSource, "kind" | "name">): string {
  return source.kind === "journal" ? "journal" : `${source.kind}:${source.name}`;
}

export function sourceLabel(source: LogSource): string {
  if (source.kind === "journal") {
    return "System journal";
  }
  if (source.kind === "file") {
    return source.name.replace(/^\/var\/log\//, "");
  }
  return source.name;
}

type Group = { title: string; sources: LogSource[] };

/** Deployed workloads first, then containers, services, files, and the whole journal. */
export function groupSources(sources: LogSource[], deployed: string[]): Group[] {
  const deployedKeys = new Set(deployed);
  const isDeployed = (source: LogSource) =>
    deployedKeys.has(sourceKey(source)) ||
    (source.kind === "docker" && !!source.project && deployedKeys.has(`compose:${source.project}`));
  const groups: Group[] = [
    { title: "Deployed by MeteorCloud", sources: sources.filter(isDeployed) },
    { title: "Containers", sources: sources.filter((s) => s.kind === "docker" && !isDeployed(s)) },
    { title: "Services", sources: sources.filter((s) => s.kind === "systemd" && !isDeployed(s)) },
    { title: "Log files", sources: sources.filter((s) => s.kind === "file") },
    { title: "System", sources: sources.filter((s) => s.kind === "journal") },
  ];
  return groups.filter((group) => group.sources.length > 0);
}
