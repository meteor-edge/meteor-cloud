import type { EnrollmentApiKey } from "@/api/fleet";

const HEADER_CELLS = /^(serial|serial[_ ]?number|serials|mac|mac[_ ]?address(es)?|macs)$/i;

/** Serials and MACs from pasted text or a CSV file: one per cell, separated by lines, commas, semicolons or spaces. */
export function parseAllowlist(text: string): string[] {
  const seen = new Set<string>();
  const entries: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const cell = raw
      .trim()
      .replace(/^"(.*)"$/, "$1")
      .trim();
    if (!cell || HEADER_CELLS.test(cell) || seen.has(cell)) {
      continue;
    }
    seen.add(cell);
    entries.push(cell);
  }
  return entries;
}

/** One line for the keys table, such as "Allow-list 120 · 34 / 500 · until 31 Dec". */
export function describeAutoApproveRule(entry: EnrollmentApiKey): string {
  const rule = entry.auto_approve_rule;
  if (!rule) {
    return "Off";
  }
  const parts: string[] = [];
  parts.push(rule.allowlist.length > 0 ? `Allow-list ${rule.allowlist.length}` : "Any device");
  parts.push(
    rule.max_devices
      ? `${rule.approved_count} / ${rule.max_devices}`
      : `${rule.approved_count} approved`,
  );
  if (rule.until) {
    parts.push(`until ${new Date(rule.until).toLocaleDateString()}`);
  }
  return parts.join(" · ");
}
