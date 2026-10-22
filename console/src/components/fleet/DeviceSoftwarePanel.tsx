import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listDeviceSoftware, type DeviceSoftware } from "@/api/fleet";
import { Button } from "@/components/ui/button";

const SYNC_LABELS: Record<string, string> = {
  in_sync: "In sync",
  pending: "Pending",
  updating: "Updating",
  failed: "Failed",
  cancelled: "Cancelled",
};

const HEALTH_LABELS: Record<string, string> = {
  healthy: "Healthy",
  unhealthy: "Unhealthy",
  unknown: "Unknown",
};

function releaseLabel(item: DeviceSoftware["desired_release"]): string {
  if (!item) {
    return "—";
  }
  return `${item.name} ${item.version}`;
}

type DeviceSoftwarePanelProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  canDeploy?: boolean;
};

export function DeviceSoftwarePanel({
  token,
  organizationId,
  deviceId,
  canDeploy = false,
}: DeviceSoftwarePanelProps) {
  const softwareQuery = useQuery({
    queryKey: ["device-software", organizationId, deviceId, token],
    queryFn: () => listDeviceSoftware(token, organizationId, deviceId),
    enabled: Boolean(token && organizationId && deviceId),
  });

  const items = softwareQuery.data ?? [];

  if (softwareQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading software…</p>;
  }

  if (softwareQuery.isError) {
    return <p className="text-sm text-destructive">Could not load software.</p>;
  }

  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-card/50 p-6 text-center">
        <p className="text-sm font-medium">No software assigned</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Deploy a release to this device to set desired software.
        </p>
        {canDeploy && (
          <Button type="button" className="mt-4" asChild>
            <Link
              to={`/organizations/${organizationId}/deployments?${new URLSearchParams({
                deploy: "1",
                device_id: deviceId,
              }).toString()}`}
            >
              Deploy a release
            </Link>
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="border-b border-border bg-muted/40 text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">Workload</th>
            <th className="px-4 py-3 font-medium">Desired</th>
            <th className="px-4 py-3 font-medium">Installed</th>
            <th className="px-4 py-3 font-medium">Sync</th>
            <th className="px-4 py-3 font-medium">Health</th>
            <th className="px-4 py-3 font-medium">Error</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.id} className="border-b border-border last:border-0">
              <td className="px-4 py-3 font-medium">{row.workload_key}</td>
              <td className="px-4 py-3">{releaseLabel(row.desired_release)}</td>
              <td className="px-4 py-3">
                {row.installed_release
                  ? releaseLabel(row.installed_release)
                  : row.installed_version || "—"}
              </td>
              <td className="px-4 py-3">
                {SYNC_LABELS[row.sync_status] ?? row.sync_status}
                {row.sync_status === "updating"
                  ? ` (${row.progress_percent}%)`
                  : ""}
              </td>
              <td className="px-4 py-3">{HEALTH_LABELS[row.health] ?? row.health}</td>
              <td className="px-4 py-3 text-muted-foreground">{row.last_error || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
