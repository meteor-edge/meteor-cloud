import { load as loadYaml } from "js-yaml";

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
