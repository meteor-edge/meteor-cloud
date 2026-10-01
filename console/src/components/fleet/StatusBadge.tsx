import type { ConnectivityStatus } from "@/api/fleet";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<ConnectivityStatus, string> = {
  online: "bg-green-100 text-green-800",
  offline: "bg-red-100 text-red-800",
  never_seen: "bg-gray-100 text-gray-700",
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
