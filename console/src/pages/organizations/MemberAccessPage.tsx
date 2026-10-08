import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getMemberAccess } from "@/api/access";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";

export function MemberAccessPage() {
  const { organizationId = "", membershipId = "" } = useParams();
  const { token } = useAuth();

  const accessQuery = useQuery({
    queryKey: ["member-access", organizationId, membershipId, token],
    queryFn: () => getMemberAccess(token!, organizationId, membershipId),
    enabled: Boolean(token && organizationId && membershipId),
  });

  if (accessQuery.isLoading) {
    return <p className="text-muted-foreground">Loading access…</p>;
  }
  if (!accessQuery.data) {
    return <p className="rounded-md bg-ember px-2 py-1 text-midnight">Member access was not found.</p>;
  }

  const access = accessQuery.data;
  const byResource = new Map<string, typeof access.permissions_detail>();
  for (const item of access.permissions_detail ?? []) {
    const list = byResource.get(item.resource) ?? [];
    list.push(item);
    byResource.set(item.resource, list);
  }

  const scopeText =
    access.scope.mode === "device_groups"
      ? access.scope.device_group_names.join(", ") || "Selected device groups"
      : "Entire organization";

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">Access</p>
          <h1 className="text-3xl font-semibold tracking-tight">{access.full_name}</h1>
          <p className="mt-2 text-muted-foreground">{access.email}</p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/members`}>Back to members</Link>
        </Button>
      </div>

      <div className="space-y-3 rounded-lg border border-border bg-card p-5 shadow-glow">
        <h2 className="text-lg font-semibold">Access Summary</h2>
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Role</dt>
            <dd className="font-medium">{access.role.name}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Organization</dt>
            <dd className="font-medium">{access.organization_name}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Device groups</dt>
            <dd className="font-medium">{scopeText}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Permissions</dt>
            <dd className="font-medium">
              {access.summary.granted_count} granted · {access.summary.denied_count} denied
            </dd>
          </div>
        </dl>
        <div>
          <p className="text-sm text-muted-foreground">Effective access</p>
          <ul className="mt-2 space-y-1 text-sm">
            {Object.entries(access.summary.by_resource).map(([resource, actions]) => (
              <li key={resource}>
                <span className="font-medium capitalize">{resource.replaceAll("_", " ")}</span>:{" "}
                {actions.length ? actions.join(", ") : "None"}
              </li>
            ))}
            {!Object.keys(access.summary.by_resource).length && <li>None</li>}
          </ul>
        </div>
      </div>

      <div className="space-y-6">
        <h2 className="text-lg font-semibold">Effective permissions</h2>
        {[...byResource.entries()].map(([resource, items]) => (
          <div key={resource} className="rounded-lg border border-border bg-card p-4 shadow-glow">
            <h3 className="mb-3 font-semibold capitalize">{resource.replaceAll("_", " ")}</h3>
            <ul className="space-y-2 text-sm">
              {items.map((item) => (
                <li key={item.id} className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className={item.granted ? "font-medium" : "text-muted-foreground"}>
                      {item.granted ? "✓" : "✗"} {item.label}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      <code>{item.id}</code>
                      {item.granted
                        ? ` · Source: ${item.role_key} role · Scope: ${
                            item.scope_mode === "device_groups" ? scopeText : "Entire organization"
                          }`
                        : " · Reason: Permission not included in role"}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
