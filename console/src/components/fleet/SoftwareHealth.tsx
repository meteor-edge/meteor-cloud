import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getSoftwareHistory, type SoftwareRange, type WorkloadHealth } from "@/api/fleet";
import type { TelemetrySeries } from "@/api/fleet/telemetry";
import { ApiError } from "@/api/http";
import { describeEvent, unavailableText } from "@/components/fleet/DeviceHistoryPanels";
import { MetricChart } from "@/components/fleet/DeviceTelemetryCharts";
import { cn, formatDateTime } from "@/lib/utils";

const RANGES: SoftwareRange[] = ["24h", "7d", "30d"];

const STYLES: Record<WorkloadHealth, string> = {
  healthy: "bg-aurora",
  unhealthy: "bg-ember",
  stopped: "bg-horizon",
  unknown: "bg-muted",
};

const LABELS: Record<WorkloadHealth, string> = {
  healthy: "Healthy",
  unhealthy: "Unhealthy",
  stopped: "Stopped by user",
  unknown: "Unknown",
};

const MIB = 1024 * 1024;

function chart(series: TelemetrySeries) {
  const memory = series.name.endsWith(".mem");
  const container = series.name.replace(/\.(cpu|mem)$/, "");
  return {
    spec: {
      name: series.name,
      label: `${container} ${memory ? "memory" : "CPU"}`,
      unit: memory ? " MiB" : "%",
      domain: null,
      digits: 1,
      floor: 0,
    },
    series: memory
      ? {
          ...series,
          points: series.points.map((p) => ({
            ...p,
            avg: p.avg / MIB,
            min: p.min / MIB,
            max: p.max / MIB,
          })),
        }
      : series,
  };
}

/** Health over time of one workload, with its events, deploys and CPU/memory. */
export function SoftwareHealth({
  token,
  organizationId,
  deviceId,
  softwareId,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
  softwareId: string;
}) {
  const [range, setRange] = useState<SoftwareRange>("24h");
  const query = useQuery({
    queryKey: ["software-history", organizationId, deviceId, softwareId, range, token],
    queryFn: () => getSoftwareHistory(token, organizationId, deviceId, softwareId, range),
    refetchInterval: 60_000,
    retry: (count, error) => !(error instanceof ApiError) && count < 1,
  });
  const data = query.data;
  const t0 = data ? Date.parse(data.start) : 0;
  const span = data ? Math.max(1, Date.parse(data.end) - t0) : 1;
  const position = (iso: string) =>
    Math.min(100, Math.max(0, ((Date.parse(iso) - t0) / span) * 100));
  const rows = data
    ? [
        ...data.changes.map((c) => ({
          t: c.t,
          what: LABELS[c.status],
          detail: c.reason ?? "",
          bad: c.status === "unhealthy",
        })),
        ...data.events.map((e) => ({
          t: e.t,
          what: describeEvent(e),
          detail: [e.app, e.detail].filter(Boolean).join(" · "),
          bad: ["app.crash_loop", "app.oom_killed", "app.unhealthy"].includes(e.type),
        })),
        ...data.deployments.map((d) => ({
          t: d.t,
          what: `Deploy ${d.status.replace(/_/g, " ")}`,
          detail: [`${d.release_name ?? ""} ${d.release_version ?? ""}`.trim(), d.error]
            .filter(Boolean)
            .join(" · "),
          bad: ["failed", "timed_out", "rolled_back"].includes(d.status),
        })),
      ].sort((a, b) => Date.parse(b.t) - Date.parse(a.t))
    : [];
  const summary = data
    ? data.healthy_percent == null
      ? "No health recorded in this range yet"
      : `${data.healthy_percent.toFixed(1)}% healthy`
    : "";

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-4 shadow-glow sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Health history</h2>
        <div role="radiogroup" aria-label="Time range" className="inline-flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={range === r}
              onClick={() => setRange(r)}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium",
                range === r ? "bg-primary text-primary-foreground" : "text-muted-foreground",
              )}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      {query.isLoading && <p className="text-sm text-muted-foreground">Loading health history…</p>}
      {query.isError && (
        <p className="text-sm text-muted-foreground">{unavailableText(query.error)}</p>
      )}
      {data && (
        <>
          <div>
            <p className="text-xs tabular-nums text-muted-foreground">{summary}</p>
            <div
              className="relative mt-2 h-4 overflow-hidden rounded bg-muted"
              role="img"
              aria-label={`Health over the last ${range}: ${summary}`}
            >
              {data.segments.map((s) => (
                <span
                  key={s.start}
                  title={`${LABELS[s.status]} · ${formatDateTime(s.start)} – ${formatDateTime(s.end)}`}
                  className={cn("absolute inset-y-0", STYLES[s.status])}
                  style={{
                    left: `${position(s.start)}%`,
                    width: `max(${position(s.end) - position(s.start)}%, 2px)`,
                  }}
                />
              ))}
              {data.deployments.map((d, i) => (
                <span
                  key={`${d.t}-${i}`}
                  title={`Deploy ${d.status} · ${formatDateTime(d.t)}`}
                  className="absolute inset-y-0 w-0.5 bg-foreground"
                  style={{ left: `${position(d.t)}%` }}
                />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-muted-foreground">
              {(Object.keys(LABELS) as WorkloadHealth[]).map((status) => (
                <span key={status} className="inline-flex items-center gap-1.5">
                  <span className={cn("h-2 w-2 rounded-sm ring-1 ring-border", STYLES[status])} />
                  {LABELS[status]}
                </span>
              ))}
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-0.5 bg-foreground" /> Deploy
              </span>
            </div>
          </div>
          {data.series.length > 0 && (
            <div className="grid gap-3 lg:grid-cols-2">
              {data.series.map(chart).map(({ spec, series }) => (
                <MetricChart
                  key={spec.name}
                  spec={spec}
                  series={series}
                  start={data.start}
                  end={data.end}
                  bucketSeconds={data.bucket_seconds}
                  range={range}
                />
              ))}
            </div>
          )}
          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing happened in this range.</p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {rows.map((row, i) => (
                <li key={`${row.t}-${i}`} className="flex flex-wrap gap-x-4 gap-y-1 py-1.5">
                  <span className="tabular-nums text-muted-foreground">
                    {formatDateTime(row.t)}
                  </span>
                  <span className={cn("font-medium", row.bad && "text-ember")}>{row.what}</span>
                  <span className="break-words text-muted-foreground">{row.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
