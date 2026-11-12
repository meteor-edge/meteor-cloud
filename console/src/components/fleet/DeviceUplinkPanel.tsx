import { useQuery } from "@tanstack/react-query";
import { Cable, Network, Signal, Wifi, type LucideIcon } from "lucide-react";

import { getDeviceWifi, type DeviceWifiStatus, type UplinkType } from "@/api/fleet";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
};

const KINDS: Record<UplinkType, { label: string; icon: LucideIcon }> = {
  ethernet: { label: "Ethernet", icon: Cable },
  wifi: { label: "Wi-Fi", icon: Wifi },
  cellular: { label: "Mobile network", icon: Signal },
  other: { label: "Other link", icon: Network },
};

function wifiName(status: DeviceWifiStatus): string | null {
  if (!status.connected_ssid) return null;
  const signal = status.signal_percent != null ? `, signal ${status.signal_percent}%` : "";
  return `${status.connected_ssid}${signal}`;
}

/** The other link that is also up, so losing the one in use does not cut the device off. */
function standby(status: DeviceWifiStatus): string | null {
  if (status.uplink_type !== "ethernet" && status.wired_connected)
    return "Ethernet cable also connected.";
  const name = wifiName(status);
  if (status.uplink_type !== "wifi" && name) return `Wi-Fi also connected: ${name}.`;
  return null;
}

/** Which link the device's traffic uses right now: Ethernet, Wi-Fi or another. */
export function DeviceUplinkPanel({ token, organizationId, deviceId }: Props) {
  // Same key as the Wi-Fi settings, so both views share one request.
  const wifiQuery = useQuery({
    queryKey: ["device-wifi", organizationId, deviceId],
    queryFn: () => getDeviceWifi(token, organizationId, deviceId),
    refetchInterval: 30000,
  });

  if (wifiQuery.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (wifiQuery.isError) {
    return <p className="text-sm text-destructive">The connection could not be loaded.</p>;
  }

  const status = wifiQuery.data?.status ?? null;
  if (!status?.uplink_type) {
    return (
      <p className="text-sm text-muted-foreground">
        Not reported yet. The device reports it with meteorcli 0.3.0 or newer while it is online.
      </p>
    );
  }

  const kind = KINDS[status.uplink_type];
  const Icon = kind.icon;
  const detail = status.uplink_type === "wifi" ? wifiName(status) : null;
  const other = standby(status);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-md border border-border">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <div>
          <p className="text-sm font-medium">
            {kind.label}
            {detail && <span className="font-normal text-muted-foreground"> · {detail}</span>}
          </p>
          <p className="text-xs text-muted-foreground">
            {status.uplink_interface && (
              <span className="font-mono">{status.uplink_interface}</span>
            )}
            {status.uplink_interface && " · "}
            <span title={formatDateTime(status.reported_at)}>
              reported {formatRelativeTime(status.reported_at)}
            </span>
          </p>
        </div>
      </div>
      {other && <p className="text-sm text-muted-foreground">{other}</p>}
    </div>
  );
}
