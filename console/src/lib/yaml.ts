import { dump as dumpYaml, load as loadYaml } from "js-yaml";

/** Serialize a plain object as YAML (the Deploy wizard's YAML preview). */
export function toYaml(value: Record<string, unknown>): string {
  return dumpYaml(value, { lineWidth: -1, noRefs: true });
}

/** Parse YAML or JSON into a plain object. Throws with a readable message. */
export function parseYamlObject(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("YAML is empty.");
  }
  let parsed: unknown;
  try {
    parsed = loadYaml(trimmed);
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : "Invalid YAML.");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("YAML must be a mapping (object) at the top level.");
  }
  return parsed as Record<string, unknown>;
}
