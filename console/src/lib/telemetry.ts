import type { FleetOnlineHistory } from "@/api/fleet/history";
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

/** Every bucket of the window, with 0 where no device reported (the data plane omits empty buckets). */
export function fillBuckets(data: FleetOnlineHistory): { t: number; devices: number }[] {
  const step = data.bucket_seconds * 1000;
  if (step <= 0) {
    return data.buckets.map((b) => ({ t: Date.parse(b.t), devices: b.devices }));
  }
  const counts = new Map(data.buckets.map((b) => [Date.parse(b.t), b.devices]));
  const first = Math.floor(Date.parse(data.start) / step) * step;
  const out: { t: number; devices: number }[] = [];
  for (let t = first; t < Date.parse(data.end); t += step) {
    out.push({ t, devices: counts.get(t) ?? 0 });
  }
  return out;
}
