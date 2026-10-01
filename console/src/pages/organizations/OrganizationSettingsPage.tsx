import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import {
  deleteOrganization,
  getOrganization,
  leaveOrganization,
  updateOrganization,
} from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { canDeleteOrganization, canUpdateOrganization } from "@/lib/permissions";

export function OrganizationSettingsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const { refreshOrganizations } = useOrganizationContext();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  useEffect(() => {
    if (orgQuery.data) {
      setName(orgQuery.data.name);
      setSlug(orgQuery.data.slug);
      setDescription(orgQuery.data.description ?? "");
    }
  }, [orgQuery.data]);

  if (orgQuery.isLoading) {
    return <p className="text-muted-foreground">Loading settings…</p>;
  }

  if (!orgQuery.data) {
    return <p className="text-red-700">Organization was not found.</p>;
  }

  const role = orgQuery.data.current_user_role;
  const canUpdate = canUpdateOrganization(role);
  const canDelete = canDeleteOrganization(role);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token || !canUpdate) {
      return;
    }
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      await updateOrganization(token, organizationId, {
        name,
        slug,
        description,
      });
      await refreshOrganizations();
      setMessage("Organization updated.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onDelete() {
    if (!token || !canDelete) {
      return;
    }
    const confirmed = window.confirm(
      `Delete organization "${orgQuery.data?.name}"? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }
    setError(null);
    try {
      await deleteOrganization(token, organizationId);
      await refreshOrganizations();
      navigate("/organizations");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed.");
    }
  }

  async function onLeave() {
    if (!token) {
      return;
    }
    const confirmed = window.confirm("Leave this organization?");
    if (!confirmed) {
      return;
    }
    setError(null);
    try {
      await leaveOrganization(token, organizationId);
      await refreshOrganizations();
      navigate("/organizations");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not leave organization.");
    }
  }

  return (
    <section className="mx-auto max-w-xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-2 text-muted-foreground">{orgQuery.data.name}</p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}`}>Back</Link>
        </Button>
      </div>

      {canUpdate ? (
        <form
          className="space-y-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm"
          onSubmit={onSubmit}
        >
          <div>
            <Label htmlFor="name">Name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor="slug">Slug</Label>
            <Input id="slug" value={slug} onChange={(e) => setSlug(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-red-700">{error}</p>}
          {message && <p className="text-sm text-primary">{message}</p>}
          <Button type="submit" disabled={submitting}>
            Save changes
          </Button>
        </form>
      ) : (
        <p className="text-sm text-muted-foreground">
          You can view this organization but cannot change its settings.
        </p>
      )}

      <div className="space-y-3 rounded-lg border border-border bg-white/80 p-6 shadow-sm">
        <h2 className="text-lg font-semibold">Danger zone</h2>
        <Button variant="outline" onClick={onLeave}>
          Leave organization
        </Button>
        {canDelete && (
          <div>
            <Button variant="ghost" className="text-red-700" onClick={onDelete}>
              Delete organization
            </Button>
          </div>
        )}
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>
    </section>
  );
}
