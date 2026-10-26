import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { listEndpoints } from "@/api/fleet";
import { useAuth } from "@/auth/AuthContext";
import { AvailabilityBadge } from "@/components/fleet/StatusBadge";

export function EndpointsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const endpointsQuery = useQuery({
    queryKey: ["endpoints", organizationId, token],
    queryFn: () => listEndpoints(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const items = endpointsQuery.data ?? [];

  return (
    <section className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Endpoints</h1>
        <p className="mt-2 text-muted-foreground">
          Public HTTPS hostnames for applications on devices. Each app/port gets its own
          URL.
        </p>
      </div>
      {endpointsQuery.isError && (
        <p className="text-sm text-destructive">Could not load endpoints.</p>
      )}
      {items.length === 0 && !endpointsQuery.isLoading ? (
        <p className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
          No public URLs yet. Publish an app when deploying to a single device, or from a
          device page.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b border-border bg-muted/40 text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Hostname</th>
                <th className="px-4 py-3 font-medium">Port</th>
                <th className="px-4 py-3 font-medium">Availability</th>
                <th className="px-4 py-3 font-medium">Enabled</th>
                <th className="px-4 py-3 font-medium">Device</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <a className="text-link underline" href={item.public_url} target="_blank" rel="noreferrer">
                      {item.hostname}
                    </a>
                  </td>
                  <td className="px-4 py-3">{item.port}</td>
                  <td className="px-4 py-3">
                    <AvailabilityBadge status={item.availability} />
                  </td>
                  <td className="px-4 py-3">{item.enabled ? "yes" : "no"}</td>
                  <td className="px-4 py-3">
                    <Link
                      className="text-link underline"
                      to={`/organizations/${organizationId}/devices/${item.device_id}`}
                    >
                      {item.device_id.slice(0, 8)}…
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
