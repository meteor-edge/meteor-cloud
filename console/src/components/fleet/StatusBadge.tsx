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
