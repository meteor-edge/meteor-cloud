import { useMemo, useState, type PointerEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { getFleetOnlineHistory } from "@/api/fleet/history";
import type { TelemetryRange } from "@/api/fleet/telemetry";
import { ApiError } from "@/api/http";
import { fillBuckets } from "@/lib/telemetry";
import { cn } from "@/lib/utils";

const RANGES: TelemetryRange[] = ["24h", "7d", "30d"];
const W = 600;
const H = 120;

function formatTick(t: number, range: TelemetryRange): string {
  const d = new Date(t);
  return range === "24h"
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** How many devices were online over time, for the organization overview. */
export function FleetOnlineChart({
  token,
  organizationId,
}: {
  token: string;
  organizationId: string;
}) {
  const [range, setRange] = useState<TelemetryRange>("24h");
  const [hover, setHover] = useState<number | null>(null);
  const query = useQuery({
    queryKey: ["fleet-online-history", organizationId, range, token],
    queryFn: () => getFleetOnlineHistory(token, organizationId, range),
    refetchInterval: 300_000,
    retry: (count, error) => !(error instanceof ApiError) && count < 1,
  });
  const data = query.data;
  const points = useMemo(() => (data ? fillBuckets(data) : []), [data]);

  // Members limited to some device groups cannot see organization-wide counts.
  if (query.error instanceof ApiError && query.error.status === 403) {
    return null;
  }

  const t0 = data ? Date.parse(data.start) : 0;
  const span = data ? Math.max(1, Date.parse(data.end) - t0) : 1;
  const top = Math.max(1, data?.total_devices ?? 0, ...points.map((p) => p.devices));
  const x = (t: number) => ((t - t0) / span) * W;
  const y = (v: number) => H - (v / top) * H;
  const half = ((data?.bucket_seconds ?? 0) * 1000) / 2;
  const line = points
    .map((p, i) => `${i ? "L" : "M"}${x(p.t + half).toFixed(1)},${y(p.devices).toFixed(1)}`)
    .join("");
  const area = points.length
    ? `${line}L${x(points[points.length - 1].t + half).toFixed(1)},${H}L${x(points[0].t + half).toFixed(1)},${H}Z`
    : "";
  const peak = points.reduce((m, p) => Math.max(m, p.devices), 0);
  const hp = hover != null ? points[hover] : null;

  function onMove(event: PointerEvent<HTMLDivElement>) {
    if (!points.length) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const vx = ((event.clientX - rect.left) / Math.max(1, rect.width)) * W;
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(x(p.t + half) - vx) < Math.abs(x(points[best].t + half) - vx)) best = i;
    });
    setHover(best);
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-meteor-cyan">
            Fleet
          </p>
          <h2 className="mt-1 text-base font-semibold">Devices online</h2>
        </div>
        <div
          role="radiogroup"
          aria-label="Devices online range"
          className="inline-flex rounded-md border border-border bg-field p-0.5"
        >
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={range === r}
              onClick={() => setRange(r)}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium tabular-nums transition-colors",
                range === r
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-[var(--tab-hover)] hover:text-foreground",
              )}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <p className="text-sm text-muted-foreground">
          {query.error instanceof ApiError && query.error.code === "telemetry_unavailable"
            ? "Online history needs the data API with NATS and ClickHouse."
            : "Online history is unavailable right now."}
        </p>
      ) : data ? (
        <>
          <p className="text-sm text-muted-foreground">
            <span className="text-2xl font-semibold tabular-nums text-foreground">
              {data.online_now}
            </span>{" "}
            of {data.total_devices} online now · peak {peak} in the last {range}
          </p>
          <div
            className="relative h-28 touch-none"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="absolute inset-0 h-full w-full overflow-visible"
              role="img"
              aria-label={`Devices online over the last ${range}: ${data.online_now} of ${data.total_devices} now, peak ${peak}`}
            >
              <line
                x1={0}
                x2={W}
                y1={y(data.total_devices)}
                y2={y(data.total_devices)}
                stroke="var(--border)"
                strokeDasharray="4 4"
                vectorEffect="non-scaling-stroke"
              />
              <line
                x1={0}
                x2={W}
                y1={H}
                y2={H}
                stroke="var(--border)"
                vectorEffect="non-scaling-stroke"
              />
              {area ? <path d={area} fill="var(--link)" fillOpacity={0.16} stroke="none" /> : null}
              {line ? (
                <path
                  d={line}
                  fill="none"
                  stroke="var(--link)"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
            </svg>
            <span className="pointer-events-none absolute right-0 -top-4 text-[10px] text-muted-foreground">
              {data.total_devices} registered
            </span>
            {hp ? (
              <div
                role="status"
                className={cn(
                  "pointer-events-none absolute top-0 z-10 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-glow",
                  x(hp.t + half) / W > 0.6 ? "-translate-x-[calc(100%+8px)]" : "translate-x-2",
                )}
                style={{ left: `${(x(hp.t + half) / W) * 100}%` }}
              >
                <p className="text-muted-foreground">{new Date(hp.t).toLocaleString()}</p>
                <p className="font-semibold tabular-nums">{hp.devices} online</p>
              </div>
            ) : null}
          </div>
          <div className="flex justify-between text-[10px] tabular-nums text-muted-foreground/80">
            <span>{formatTick(t0, range)}</span>
            <span>{formatTick(t0 + span / 2, range)}</span>
            <span>{formatTick(t0 + span, range)}</span>
          </div>
        </>
      ) : null}
    </div>
  );
}
