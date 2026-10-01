import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { canManageMembers, canUpdateOrganization } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export function OrganizationOverviewPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  if (orgQuery.isLoading) {
    return <p className="text-muted-foreground">Loading organization…</p>;
  }

  if (orgQuery.isError || !orgQuery.data) {
    return <p className="text-red-700">Organization was not found.</p>;
  }

  const organization = orgQuery.data;

  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{organization.name}</h1>
          <p className="mt-2 text-muted-foreground">{organization.slug}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" asChild>
            <Link to={`/organizations/${organization.id}/devices`}>Fleet</Link>
          </Button>
          <Button variant="secondary" asChild>
            <Link to={`/organizations/${organization.id}/members`}>Members</Link>
          </Button>
          {canUpdateOrganization(organization.current_user_role) && (
            <Button variant="outline" asChild>
              <Link to={`/organizations/${organization.id}/settings`}>Settings</Link>
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm sm:grid-cols-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Your role
          </p>
          <p className="mt-1 text-lg font-medium capitalize">{organization.current_user_role}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Members
          </p>
          <p className="mt-1 text-lg font-medium">{organization.member_count ?? "—"}</p>
        </div>
        <div className="sm:col-span-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Description
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {organization.description || "No description provided."}
          </p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Created
          </p>
          <p className="mt-1 text-sm">{formatDateTime(organization.created_at)}</p>
        </div>
      </div>

      {canManageMembers(organization.current_user_role) && (
        <p className="text-sm text-muted-foreground">
          You can manage members from the members page.
        </p>
      )}
    </section>
  );
}
