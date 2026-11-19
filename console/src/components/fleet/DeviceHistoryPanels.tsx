import { useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  getDeviceApps,
  getDeviceAvailability,
  getDeviceDeploymentHistory,
  getDeviceLogHistory,
  type AppEvent,
  type Connectivity,
} from "@/api/fleet/history";
import type { TelemetryRange } from "@/api/fleet/telemetry";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, formatBytes, formatDateTime, formatDuration } from "@/lib/utils";

// History of one device kept by the data plane: availability (in the telemetry
// panel), deployment status changes, apps, and saved system warnings.

const noRetryOnApiError = (count: number, error: unknown) =>
  !(error instanceof ApiError) && count < 1;

function unavailableText(error: unknown): string {
  if (error instanceof ApiError && error.code === "telemetry_unavailable") {
    return "History is not enabled on this installation. It needs the data API with NATS and ClickHouse.";
  }
  return "History is unavailable right now.";
}

const SEGMENT_STYLES: Record<Connectivity, string> = {
  online: "bg-aurora",
  offline: "bg-ember",
  unknown: "bg-muted",
};

/** Online/offline over the window as one strip, with uptime and how often it went offline. */
export function AvailabilityStrip({
  token,
  organizationId,
  deviceId,
  range,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
  range: TelemetryRange;
}) {
  const query = useQuery({
    queryKey: ["device-availability", organizationId, deviceId, range, token],
    queryFn: () => getDeviceAvailability(token, organizationId, deviceId, range),
    refetchInterval: 60_000,
    retry: noRetryOnApiError,
  });
  const data = query.data;
  if (!data) {
    return null;
  }
  const t0 = Date.parse(data.start);
  const span = Math.max(1, Date.parse(data.end) - t0);
  const knownSeconds = data.segments.reduce((sum, s) => {
    if (s.status === "unknown") return sum;
    return sum + Math.max(0, Date.parse(s.end) - Date.parse(s.start)) / 1000;
  }, 0);
  const summary =
    data.uptime_percent == null
      ? "No connect/disconnect messages in this range yet"
      : knownSeconds < 60
        ? `Just started recording · ${data.uptime_percent.toFixed(0)}% online so far`
        : `${data.uptime_percent.toFixed(1)}% online · went offline ${data.offline_count} ${
            data.offline_count === 1 ? "time" : "times"
          }`;
  return (
    <div className="rounded-xl border border-border bg-field/80 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          Availability
        </span>
        <span className="text-xs tabular-nums text-muted-foreground">{summary}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Connect and disconnect over time. Grey means we have not recorded a status message yet
        (different from the Online badge above, which uses recent heartbeats).
      </p>
      <div
        className="relative mt-3 h-4 overflow-hidden rounded bg-muted"
        role="img"
        aria-label={`Availability over the last ${range}: ${summary}`}
      >
        {data.segments.map((s) => {
          const left = ((Date.parse(s.start) - t0) / span) * 100;
          const width = ((Date.parse(s.end) - Date.parse(s.start)) / span) * 100;
          const label =
            s.status === "online" ? "Online" : s.status === "offline" ? "Offline" : "No data yet";
          return (
            <span
              key={s.start}
              title={`${label} · ${formatDateTime(s.start)} – ${formatDateTime(s.end)}`}
              className={cn("absolute inset-y-0", SEGMENT_STYLES[s.status])}
              style={{ left: `${left}%`, width: `max(${width}%, 2px)` }}
            />
          );
        })}
      </div>
      <div className="mt-2 flex gap-4 text-[10px] text-muted-foreground">
        {(
          [
            ["online", "Online"],
            ["offline", "Offline"],
            ["unknown", "No data yet"],
          ] as const
        ).map(([status, label]) => (
          <span key={status} className="inline-flex items-center gap-1.5">
            <span className={cn("h-2 w-2 rounded-sm ring-1 ring-border", SEGMENT_STYLES[status])} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  succeeded: "text-aurora",
  failed: "text-ember",
  timed_out: "text-ember",
  rolled_back: "text-ember",
  cancelled: "text-muted-foreground",
  skipped: "text-muted-foreground",
};

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

/** Every status change of this device in a Deploy, newest first, with how long it took. */
export function DeploymentHistoryPanel({
  token,
  organizationId,
  deviceId,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
}) {
  const query = useQuery({
    queryKey: ["device-deployment-history", organizationId, deviceId, token],
    queryFn: () => getDeviceDeploymentHistory(token, organizationId, deviceId),
    // Keep a deployment that is still installing up to date while the panel is open.
    refetchInterval: 15_000,
    retry: noRetryOnApiError,
  });
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-6 shadow-glow">
      <h2 className="text-lg font-semibold">Deployment history</h2>
      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading deployment history…</p>
      ) : query.isError ? (
        <p className="text-sm text-muted-foreground">{unavailableText(query.error)}</p>
      ) : !query.data?.items.length ? (
        <p className="text-sm text-muted-foreground">
          No deployment changes recorded for this device yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1.5 pr-3 font-medium">When</th>
                <th className="py-1.5 pr-3 font-medium">Release</th>
                <th className="py-1.5 pr-3 font-medium">Status</th>
                <th className="py-1.5 font-medium">Took</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((item, i) => (
                <tr key={`${item.t}-${i}`} className="border-t border-border align-top">
                  <td className="whitespace-nowrap py-1.5 pr-3 tabular-nums text-muted-foreground">
                    {formatDateTime(item.t)}
                  </td>
                  <td className="py-1.5 pr-3">
                    {item.release_name
                      ? `${item.release_name} ${item.release_version ?? ""}`.trim()
                      : (item.deployment_name ?? "—")}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className={cn("font-medium capitalize", STATUS_TONE[item.status])}>
                      {statusLabel(item.status)}
                    </span>
                    {item.error ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">{item.error}</p>
                    ) : null}
                  </td>
                  <td className="py-1.5 tabular-nums text-muted-foreground">
                    {item.seconds != null ? formatDuration(Math.round(item.seconds)) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const EVENT_LABELS: Record<string, string> = {
  "app.started": "Started",
  "app.exited": "Exited",
  "app.restarted": "Restarted",
  "app.crash_loop": "Crash loop",
  "app.oom_killed": "Out of memory",
  "app.unhealthy": "Health check failing",
  "app.healthy": "Healthy again",
  "app.removed": "Removed",
};

const BAD_EVENTS = new Set(["app.crash_loop", "app.oom_killed", "app.unhealthy"]);

function describeEvent(event: AppEvent): string {
  const parts = [EVENT_LABELS[event.type] ?? event.type.replace(/^app\./, "")];
  if (event.exit_code != null && event.type === "app.exited") parts.push(`exit ${event.exit_code}`);
  if (event.oom_killed && event.type === "app.exited") parts.push("out of memory");
  return parts.join(" · ");
}

/** Apps (containers) on the device now, and what happened to them in the last 7 days. */
export function DeviceAppsPanel({
  token,
  organizationId,
  deviceId,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
}) {
  const query = useQuery({
    queryKey: ["device-apps", organizationId, deviceId, token],
    queryFn: () => getDeviceApps(token, organizationId, deviceId),
    refetchInterval: 60_000,
    retry: noRetryOnApiError,
  });
  const data = query.data;
  // Nothing to show on devices without containers or without the data API.
  if (!data || (!data.apps.length && !data.events.length)) {
    return null;
  }
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">Apps</h2>
        {data.reported_at ? (
          <span className="text-xs text-muted-foreground">
            Reported {formatDateTime(data.reported_at)}
          </span>
        ) : null}
      </div>
      {data.apps.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm tabular-nums">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1.5 pr-3 font-medium">App</th>
                <th className="py-1.5 pr-3 font-medium">State</th>
                <th className="py-1.5 pr-3 font-medium">CPU</th>
                <th className="py-1.5 pr-3 font-medium">Memory</th>
                <th className="py-1.5 font-medium">Restarts</th>
              </tr>
            </thead>
            <tbody>
              {data.apps.map((app) => {
                const state =
                  app.running === false
                    ? "Stopped"
                    : app.healthy === false
                      ? "Unhealthy"
                      : app.healthy
                        ? "Healthy"
                        : "Running";
                return (
                  <tr key={app.name} className="border-t border-border">
                    <td className="py-1.5 pr-3 font-medium">{app.name}</td>
                    <td
                      className={cn(
                        "py-1.5 pr-3",
                        state === "Stopped" || state === "Unhealthy" ? "text-ember" : "text-aurora",
                      )}
                    >
                      {state}
                      {app.health_ms != null ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          ({Math.round(app.health_ms)} ms check)
                        </span>
                      ) : null}
                    </td>
                    <td className="py-1.5 pr-3">
                      {app.cpu_percent != null ? `${app.cpu_percent.toFixed(1)}%` : "—"}
                    </td>
                    <td className="py-1.5 pr-3">
                      {app.memory_bytes != null ? formatBytes(app.memory_bytes) : "—"}
                    </td>
                    <td className="py-1.5">{app.restarts ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {data.events.length ? (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Recent changes
          </p>
          <ul className="mt-2 space-y-1 text-sm">
            {data.events.slice(0, 15).map((event, i) => (
              <li key={`${event.t}-${i}`} className="flex flex-wrap gap-x-3">
                <span className="tabular-nums text-muted-foreground">
                  {formatDateTime(event.t)}
                </span>
                <span className="font-medium">{event.app ?? "—"}</span>
                <span className={BAD_EVENTS.has(event.type) ? "text-ember" : undefined}>
                  {describeEvent(event)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

const PRIORITY_LABELS = ["emerg", "alert", "crit", "error", "warning"];

/** Saved system warnings (journal, warning or worse), searchable over 30 days. */
export function DeviceLogHistory({
  token,
  organizationId,
  deviceId,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
}) {
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [range, setRange] = useState<TelemetryRange>("7d");
  const query = useInfiniteQuery({
    queryKey: ["device-log-history", organizationId, deviceId, range, search, token],
    queryFn: ({ pageParam }) =>
      getDeviceLogHistory(token, organizationId, deviceId, {
        range,
        q: search || undefined,
        cursor: pageParam || undefined,
      }),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: noRetryOnApiError,
  });
  const entries = query.data?.pages.flatMap((page) => page.entries) ?? [];

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSearch(draft.trim());
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div>
        <h2 className="text-lg font-semibold">Saved warnings</h2>
        <p className="text-sm text-muted-foreground">
          Warnings and errors from the device's system journal, kept in the cloud so you can search
          them after the device is gone or offline.
        </p>
      </div>
      <form onSubmit={onSubmit} className="flex flex-wrap gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search messages"
          aria-label="Search saved warnings"
          maxLength={200}
          className="min-w-0 flex-1"
        />
        <select
          value={range}
          onChange={(e) => setRange(e.target.value as TelemetryRange)}
          aria-label="Saved warnings range"
          className="rounded-md border border-border bg-field px-2 text-sm"
        >
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </select>
        <Button type="submit" variant="secondary">
          Search
        </Button>
      </form>
      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : query.isError && !entries.length ? (
        <p className="text-sm text-muted-foreground">{unavailableText(query.error)}</p>
      ) : !entries.length ? (
        <p className="text-sm text-muted-foreground">
          {search ? `No saved warnings match “${search}”.` : "No warnings saved in this range."}
        </p>
      ) : (
        <ul className="max-h-96 space-y-1 overflow-auto rounded-md bg-background p-3 font-mono text-xs">
          {entries.map((entry, i) => (
            <li key={`${entry.t}-${i}`} className="break-words">
              <span className="text-muted-foreground">{formatDateTime(entry.t)}</span>{" "}
              {entry.priority != null && entry.priority <= 3 ? (
                <span className="text-ember">{PRIORITY_LABELS[entry.priority] ?? "error"}</span>
              ) : (
                <span className="text-muted-foreground">warning</span>
              )}{" "}
              {entry.unit || entry.source ? (
                <span className="text-link">{entry.unit || entry.source}</span>
              ) : null}{" "}
              <span className="text-foreground">{entry.message}</span>
            </li>
          ))}
        </ul>
      )}
      {query.isFetchNextPageError ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted-foreground">Older warnings could not be loaded.</p>
          <Button variant="outline" size="sm" onClick={() => query.fetchNextPage()}>
            Try again
          </Button>
        </div>
      ) : query.hasNextPage ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
        >
          Load older
        </Button>
      ) : null}
    </div>
  );
}
