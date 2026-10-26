import type { LogSource } from "@/api/fleet/types";

/** Lines kept in the browser; older ones are dropped as new ones arrive. */
export const MAX_BUFFER_LINES = 20_000;

// Colors and cursor moves from containers that log to a terminal.
// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;

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

/** A live filter: plain text (case-insensitive), or /regex/ when wrapped in slashes. */
export function buildMatcher(filter: string): RegExp | null {
  const text = filter.trim();
  if (!text) {
    return null;
  }
  const regex = /^\/(.+)\/$/.exec(text);
  try {
    return new RegExp(regex ? regex[1] : text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  } catch {
    return null;
  }
}

/**
 * Append decoded output to ``buffer`` (complete lines only, colors stripped, capped at
 * MAX_BUFFER_LINES) and return the unfinished last line to carry into the next chunk.
 */
export function appendLines(buffer: string[], partial: string, text: string): string {
  const chunks = (partial + text.replace(ANSI, "")).split(/\r?\n/);
  const rest = chunks.pop() ?? "";
  buffer.push(...chunks);
  if (buffer.length > MAX_BUFFER_LINES) {
    buffer.splice(0, buffer.length - MAX_BUFFER_LINES);
  }
  return rest;
}
