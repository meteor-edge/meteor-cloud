import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listDevices, type DeviceListParams } from "@/api/fleet";
import { StatusBadge } from "@/components/fleet/StatusBadge";
import { formatRelativeTime } from "@/lib/utils";

const PAGE_SIZE = 100;

/** Read-only device list for a group or device type detail page. */
export function DeviceListTable({
  token,
  organizationId,
  filter,
  secondaryColumn,
}: {
  token: string;
  organizationId: string;
  filter: Pick<DeviceListParams, "device_type_id" | "device_group_id">;
  /** The "other" classification to show next to each device. */
  secondaryColumn: {
    label: string;
    names: Map<string, string>;
    key: "device_type_id" | "device_group_id";
  };
}) {
  const params: DeviceListParams = { ...filter, page: 1, page_size: PAGE_SIZE, sort: "name" };
  const devicesQuery = useQuery({
    queryKey: ["devices", organizationId, params, token],
    queryFn: () => listDevices(token, organizationId, params),
  });
  const devices = devicesQuery.data?.items ?? [];
  const total = devicesQuery.data?.total ?? 0;

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">{secondaryColumn.label}</th>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Last Seen</th>
            </tr>
          </thead>
          <tbody>
            {devicesQuery.isLoading && (
              <tr>
                <td className="px-4 py-6 text-center text-muted-foreground" colSpan={4}>
                  Loading devices…
                </td>
              </tr>
            )}
            {devicesQuery.isError && (
              <tr>
                <td className="px-4 py-6 text-center text-muted-foreground" colSpan={4}>
                  Could not load devices.
                </td>
              </tr>
            )}
            {!devicesQuery.isLoading && !devicesQuery.isError && devices.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-center text-muted-foreground" colSpan={4}>
                  No devices yet.
                </td>
              </tr>
            )}
            {devices.map((device) => {
              const secondaryId = device[secondaryColumn.key];
              return (
                <tr key={device.id} className="border-b border-border">
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/organizations/${organizationId}/devices/${device.id}`}
                      className="text-foreground hover:text-link"
                    >
                      {device.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {secondaryId ? (secondaryColumn.names.get(secondaryId) ?? "—") : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={device.status} />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {device.last_seen_at ? formatRelativeTime(device.last_seen_at) : "Never"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {total > devices.length && (
        <p className="text-sm text-muted-foreground">
          Showing {devices.length} of {total} devices.
        </p>
      )}
    </div>
  );
}
