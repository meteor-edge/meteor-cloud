import type { TelemetryPoint } from "@/api/fleet/telemetry";

/** Splits the series where buckets are missing (device offline), so a gap is not drawn as a line. */
export function splitSegments(points: TelemetryPoint[], bucketSeconds: number): TelemetryPoint[][] {
  const out: TelemetryPoint[][] = [];
  let current: TelemetryPoint[] = [];
  let last = -Infinity;
  const maxGap = bucketSeconds * 1000 * 1.5;
  for (const p of points) {
    const t = Date.parse(p.t);
    if (current.length && t - last > maxGap) {
      out.push(current);
      current = [];
    }
    current.push(p);
    last = t;
  }
  if (current.length) {
    out.push(current);
  }
  return out;
}
