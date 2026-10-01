import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { formatDateTime } from "@/lib/utils";

export function OrganizationListPage() {
  const { organizations, isLoading, selectOrganization } = useOrganizationContext();

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Organizations</h1>
          <p className="mt-2 text-muted-foreground">Organizations available to your account.</p>
        </div>
        <Button asChild>
          <Link to="/organizations/new">Create organization</Link>
        </Button>
      </div>

      {isLoading && <p className="text-muted-foreground">Loading organizations…</p>}

      {!isLoading && organizations.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-white/70 p-8 text-center">
          <p className="font-medium">No organizations yet</p>
          <p className="mt-2 text-sm text-muted-foreground">
            Create one to start managing members and roles.
          </p>
        </div>
      )}

      <ul className="space-y-3">
        {organizations.map((organization) => (
          <li
            key={organization.id}
            className="rounded-lg border border-border bg-white/80 p-5 shadow-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <Link
                  to={`/organizations/${organization.id}`}
                  className="text-lg font-semibold text-foreground hover:text-primary"
                  onClick={() => selectOrganization(organization.id)}
                >
                  {organization.name}
                </Link>
                <p className="mt-1 text-sm text-muted-foreground">
                  {organization.slug} · role: {organization.current_user_role} · created{" "}
                  {formatDateTime(organization.created_at)}
                </p>
                {organization.description && (
                  <p className="mt-2 text-sm text-muted-foreground">{organization.description}</p>
                )}
              </div>
              <Button variant="secondary" asChild>
                <Link
                  to={`/organizations/${organization.id}`}
                  onClick={() => selectOrganization(organization.id)}
                >
                  Open
                </Link>
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
