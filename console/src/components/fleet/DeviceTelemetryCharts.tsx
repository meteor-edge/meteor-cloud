import { useMemo, useState, type PointerEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getDeviceTelemetry,
  type TelemetryPoint,
  type TelemetryRange,
  type TelemetrySeries,
} from "@/api/fleet/telemetry";
import { ApiError } from "@/api/http";
import { AvailabilityStrip } from "@/components/fleet/DeviceHistoryPanels";
import { splitSegments } from "@/lib/telemetry";
import { cn } from "@/lib/utils";

const RANGES: { value: TelemetryRange; label: string }[] = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

type MetricSpec = {
  name: string;
  label: string;
  unit: string;
  /** Fixed domain, or null to fit the data. */
  domain: [number, number] | null;
  digits: number;
};

const METRICS: MetricSpec[] = [
  { name: "cpu_percent", label: "CPU", unit: "%", domain: [0, 100], digits: 1 },
  { name: "memory_percent", label: "Memory", unit: "%", domain: [0, 100], digits: 1 },
  { name: "disk_percent", label: "Disk", unit: "%", domain: [0, 100], digits: 1 },
  { name: "temperature_c", label: "Temperature", unit: " °C", domain: null, digits: 1 },
  { name: "load_1m", label: "Load (1 min)", unit: "", domain: null, digits: 2 },
];

// Plot geometry in viewBox units. The SVG stretches to its box; strokes keep
// their width (non-scaling-stroke) and every text label is HTML, so nothing
// distorts at any width.
const W = 600;
const H = 160;

function formatValue(value: number, spec: MetricSpec): string {
  return `${value.toFixed(spec.digits)}${spec.unit}`;
}

function formatTick(iso: string, range: TelemetryRange): string {
  const d = new Date(iso);
  if (range === "7d" || range === "30d") {
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function niceDomain(points: TelemetryPoint[], spec: MetricSpec): [number, number] {
  if (spec.domain) {
    return spec.domain;
  }
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of points) {
    lo = Math.min(lo, p.min);
    hi = Math.max(hi, p.max);
  }
  if (!Number.isFinite(lo)) {
    return [0, 1];
  }
  if (spec.name.startsWith("load")) {
    return [0, Math.max(1, Math.ceil(hi))];
  }
  const pad = Math.max(2, (hi - lo) * 0.15);
  return [Math.floor((lo - pad) / 5) * 5, Math.ceil((hi + pad) / 5) * 5];
}

type Geometry = {
  x: (t: number) => number;
  y: (v: number) => number;
  segments: TelemetryPoint[][];
};

function geometry(
  points: TelemetryPoint[],
  start: number,
  end: number,
  domain: [number, number],
  bucketSeconds: number,
): Geometry {
  const span = Math.max(1, end - start);
  const [lo, hi] = domain;
  const half = (bucketSeconds * 1000) / 2; // plot each bucket at its middle
  return {
    x: (t) => ((t + half - start) / span) * W,
    y: (v) => H - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1)) * H,
    segments: splitSegments(points, bucketSeconds),
  };
}

function MetricChart({
  spec,
  series,
  start,
  end,
  bucketSeconds,
  range,
}: {
  spec: MetricSpec;
  series: TelemetrySeries;
  start: string;
  end: string;
  bucketSeconds: number;
  range: TelemetryRange;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const points = series.points;
  const t0 = Date.parse(start);
  const t1 = Date.parse(end);
  const domain = niceDomain(points, spec);
  const g = useMemo(
    () => geometry(points, t0, t1, domain, bucketSeconds),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [points, t0, t1, domain[0], domain[1], bucketSeconds],
  );
  const latest = points.length ? points[points.length - 1] : null;
  const peak = points.reduce((m, p) => Math.max(m, p.max), -Infinity);

  const line = (seg: TelemetryPoint[]) =>
    seg
      .map((p, i) => `${i ? "L" : "M"}${g.x(Date.parse(p.t)).toFixed(1)},${g.y(p.avg).toFixed(1)}`)
      .join("");
  const band = (seg: TelemetryPoint[]) => {
    const top = seg.map((p) => `${g.x(Date.parse(p.t)).toFixed(1)},${g.y(p.max).toFixed(1)}`);
    const bottom = [...seg]
      .reverse()
      .map((p) => `${g.x(Date.parse(p.t)).toFixed(1)},${g.y(p.min).toFixed(1)}`);
    return `M${top.join("L")}L${bottom.join("L")}Z`;
  };

  function onMove(event: PointerEvent<HTMLDivElement>) {
    if (!points.length) {
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const vx = ((event.clientX - rect.left) / Math.max(1, rect.width)) * W;
    let best = 0;
    let bestDist = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(g.x(Date.parse(p.t)) - vx);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    setHover(best);
  }

  const hp = hover != null ? points[hover] : null;
  const hx = hp ? (g.x(Date.parse(hp.t)) / W) * 100 : 0;
  const ticks = [domain[1], (domain[0] + domain[1]) / 2, domain[0]];

  return (
    <figure className="rounded-xl border border-border bg-field/80 p-4">
      <figcaption className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {spec.label}
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {latest ? (
            <>
              <span className="text-base font-semibold text-foreground">
                {formatValue(latest.avg, spec)}
              </span>{" "}
              latest · peak {formatValue(peak, spec)}
            </>
          ) : (
            "No data in this range"
          )}
        </span>
      </figcaption>

      <div className="mt-3 flex gap-2">
        <div
          className="flex h-32 w-9 shrink-0 flex-col justify-between text-right text-[10px] tabular-nums text-muted-foreground/80"
          aria-hidden
        >
          {ticks.map((v) => (
            <span key={v} className="-my-1.5 leading-none">
              {Number.isInteger(v) ? v : v.toFixed(1)}
            </span>
          ))}
        </div>
        <div
          className="relative h-32 min-w-0 flex-1 touch-none"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          data-testid={`chart-${spec.name}`}
        >
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full overflow-visible"
            role="img"
            aria-label={
              latest
                ? `${spec.label} over the last ${range}: latest ${formatValue(latest.avg, spec)}, peak ${formatValue(peak, spec)}`
                : `${spec.label}: no data in the last ${range}`
            }
          >
            {[0, 0.5, 1].map((f) => (
              <line
                key={f}
                x1={0}
                x2={W}
                y1={f * H}
                y2={f * H}
                stroke="var(--border)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {g.segments.map((seg, i) => (
              <g key={i}>
                <path d={band(seg)} fill="var(--link)" fillOpacity={0.16} stroke="none" />
                {seg.length === 1 ? (
                  <circle
                    cx={g.x(Date.parse(seg[0].t))}
                    cy={g.y(seg[0].avg)}
                    r={2.5}
                    fill="var(--link)"
                  />
                ) : (
                  <path
                    d={line(seg)}
                    fill="none"
                    stroke="var(--link)"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    vectorEffect="non-scaling-stroke"
                  />
                )}
              </g>
            ))}
          </svg>

          {hp ? (
            <>
              <div
                className="pointer-events-none absolute inset-y-0 w-px bg-muted-foreground/60"
                style={{ left: `${hx}%` }}
              />
              <div
                className="pointer-events-none absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-[var(--link)]"
                style={{ left: `${hx}%`, top: `${(g.y(hp.avg) / H) * 100}%` }}
              />
              <div
                role="status"
                className={cn(
                  "pointer-events-none absolute top-0 z-10 min-w-36 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-glow backdrop-blur",
                  hx > 60 ? "-translate-x-[calc(100%+8px)]" : "translate-x-2",
                )}
                style={{ left: `${hx}%` }}
              >
                <p className="text-muted-foreground">{formatWhen(hp.t)}</p>
                <p className="font-semibold tabular-nums text-foreground">
                  {formatValue(hp.avg, spec)} avg
                </p>
                <p className="tabular-nums text-muted-foreground">
                  {formatValue(hp.min, spec)} – {formatValue(hp.max, spec)}
                </p>
              </div>
            </>
          ) : null}
        </div>
      </div>

      <div
        className="ml-11 mt-1.5 flex justify-between text-[10px] tabular-nums text-muted-foreground/80"
        aria-hidden
      >
        <span>{formatTick(start, range)}</span>
        <span>{formatTick(new Date((t0 + t1) / 2).toISOString(), range)}</span>
        <span>{formatTick(end, range)}</span>
      </div>

      {points.length ? (
        <details className="mt-2 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">Show data</summary>
          <div className="mt-2 max-h-48 overflow-auto">
            <table className="w-full text-left tabular-nums">
              <thead>
                <tr>
                  <th className="py-1 font-medium">Time</th>
                  <th className="py-1 font-medium">Avg</th>
                  <th className="py-1 font-medium">Min</th>
                  <th className="py-1 font-medium">Max</th>
                </tr>
              </thead>
              <tbody>
                {[...points].reverse().map((p) => (
                  <tr key={p.t} className="border-t border-border">
                    <td className="py-1">{formatWhen(p.t)}</td>
                    <td className="py-1 text-foreground">{formatValue(p.avg, spec)}</td>
                    <td className="py-1">{formatValue(p.min, spec)}</td>
                    <td className="py-1">{formatValue(p.max, spec)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </figure>
  );
}

/** CPU, memory, disk, temperature and load history from ClickHouse, with a range picker. */
export function DeviceTelemetryCharts({
  token,
  organizationId,
  deviceId,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
}) {
  const [range, setRange] = useState<TelemetryRange>("24h");
  const query = useQuery({
    queryKey: ["device-telemetry", organizationId, deviceId, range, token],
    queryFn: () => getDeviceTelemetry(token, organizationId, deviceId, range),
    refetchInterval: range === "1h" ? 60_000 : 300_000,
    // A refused or unconfigured request will not get better by retrying.
    retry: (count, error) => !(error instanceof ApiError) && count < 1,
  });

  const data = query.data;
  const byName = new Map((data?.series ?? []).map((s) => [s.name, s]));
  // Always chart CPU, memory and disk; temperature and load only when the device reports them.
  const charts = METRICS.filter(
    (m) => m.domain !== null || (byName.get(m.name)?.points.length ?? 0) > 0,
  );
  const empty = data && data.series.every((s) => s.points.length === 0);

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-4 shadow-glow sm:p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-meteor-cyan">
            Telemetry
          </p>
          <h2 className="mt-1 text-base font-semibold text-foreground">History</h2>
        </div>
        <div
          role="radiogroup"
          aria-label="Time range"
          className="inline-flex rounded-md border border-border bg-field p-0.5"
        >
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              role="radio"
              aria-checked={range === r.value}
              onClick={() => setRange(r.value)}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium tabular-nums transition-colors",
                range === r.value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-[var(--tab-hover)] hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <AvailabilityStrip
        token={token}
        organizationId={organizationId}
        deviceId={deviceId}
        range={range}
      />

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading telemetry…</p>
      ) : query.isError ? (
        <p className="text-sm text-muted-foreground">
          {query.error instanceof ApiError && query.error.code === "telemetry_unavailable"
            ? "Telemetry history is not enabled on this installation. It needs the data API with NATS and ClickHouse."
            : "Telemetry history is unavailable right now."}
        </p>
      ) : empty ? (
        <p className="text-sm text-muted-foreground">
          No telemetry stored for this range yet. The agent reports every minute once it is
          connected over MQTT.
        </p>
      ) : data ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {charts.map((spec) => (
            <MetricChart
              key={spec.name}
              spec={spec}
              series={byName.get(spec.name) ?? { name: spec.name, points: [] }}
              start={data.start}
              end={data.end}
              bucketSeconds={data.bucket_seconds}
              range={range}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
