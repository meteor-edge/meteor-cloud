import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "@/api/http";
import { createOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { slugify } from "@/lib/permissions";

export function OrganizationCreatePage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { refreshOrganizations, selectOrganization } = useOrganizationContext();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!slugTouched) {
      setSlug(slugify(name));
    }
  }, [name, slugTouched]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token) {
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const organization = await createOrganization(token, {
        name,
        slug,
        description: description || undefined,
      });
      await refreshOrganizations();
      selectOrganization(organization.id);
      navigate(`/organizations/${organization.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create organization.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Create organization</h1>
        <p className="mt-2 text-muted-foreground">
          You will become the owner of the new organization.
        </p>
      </div>
      <form
        className="space-y-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm"
        onSubmit={onSubmit}
      >
        <div>
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </div>
        <div>
          <Label htmlFor="slug">Slug</Label>
          <Input
            id="slug"
            value={slug}
            onChange={(event) => {
              setSlugTouched(true);
              setSlug(event.target.value);
            }}
            required
          />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <Input
            id="description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        {error && <p className="text-sm text-red-700">{error}</p>}
        <Button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create organization"}
        </Button>
      </form>
    </section>
  );
}
