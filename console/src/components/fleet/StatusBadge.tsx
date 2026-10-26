import type { ConnectivityStatus } from "@/api/fleet";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<ConnectivityStatus, string> = {
  online: "bg-aurora text-midnight",
  offline: "bg-ember text-midnight",
  never_seen: "bg-horizon text-star-dust",
};

const STATUS_LABELS: Record<ConnectivityStatus, string> = {
  online: "Online",
  offline: "Offline",
  never_seen: "Never seen",
};

export function StatusBadge({ status }: { status: ConnectivityStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        STATUS_STYLES[status],
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

const AVAILABILITY_STYLES: Record<string, string> = {
  ready: "bg-aurora text-midnight",
  unhealthy: "bg-ember text-midnight",
  offline: "bg-horizon text-star-dust",
  unknown: "bg-muted text-muted-foreground",
};

export function AvailabilityBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        AVAILABILITY_STYLES[status] ?? AVAILABILITY_STYLES.unknown,
      )}
    >
      {status}
    </span>
  );
}

const HEALTH_STYLES: Record<string, string> = {
  healthy: "bg-aurora text-midnight",
  unhealthy: "bg-ember text-midnight",
  unknown: "bg-muted text-muted-foreground",
  stale: "bg-horizon text-star-dust",
};

const HEALTH_LABELS: Record<string, string> = {
  healthy: "Healthy",
  unhealthy: "Unhealthy",
  unknown: "Unknown",
  stale: "Stale",
};

/** Runtime health of a workload. ``stale``: the device is offline, so the last report may be old. */
export function HealthBadge({ status, stale = false }: { status: string | null; stale?: boolean }) {
  const key = stale ? "stale" : (status ?? "unknown");
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        HEALTH_STYLES[key] ?? HEALTH_STYLES.unknown,
      )}
      title={stale && status ? `Last reported: ${HEALTH_LABELS[status] ?? status}` : undefined}
    >
      {HEALTH_LABELS[key] ?? key}
    </span>
  );
}
