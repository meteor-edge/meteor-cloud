import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { createDeviceGroup, listDeviceGroups } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { DevicesNav } from "@/components/fleet/DevicesNav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageFleet } from "@/lib/permissions";

export function DeviceGroupsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  const createMutation = useMutation({
    mutationFn: () =>
      createDeviceGroup(token!, organizationId, {
        name,
        description: description || undefined,
      }),
    onSuccess: async () => {
      setName("");
      setDescription("");
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["device-groups", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create device group.");
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  const groups = groupsQuery.data ?? [];

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Device Groups</h1>
        <p className="mt-2 text-muted-foreground">
          Organize your fleet logically, e.g. Production, Testing, or Berlin Heating.
        </p>
      </div>
      <DevicesNav organizationId={organizationId} />

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow md:grid-cols-[1fr_1fr_auto]"
          onSubmit={onCreate}
        >
          <div>
            <Label htmlFor="group-name">Name</Label>
            <Input
              id="group-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Production"
              required
            />
          </div>
          <div>
            <Label htmlFor="group-description">Description</Label>
            <Input
              id="group-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Add group
            </Button>
          </div>
        </form>
      )}

      {error && (
        <p className="text-sm font-medium text-midnight bg-ember rounded-md px-2 py-1">{error}</p>
      )}

      {groupsQuery.isLoading ? (
        <p className="text-muted-foreground">Loading device groups…</p>
      ) : groups.length === 0 ? (
        <p className="text-muted-foreground">No device groups yet.</p>
      ) : (
        <ul className="space-y-3">
          {groups.map((group) => (
            <li key={group.id}>
              <Link
                to={`/organizations/${organizationId}/device-groups/${group.id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4 shadow-glow transition hover:border-link"
              >
                <div>
                  <p className="font-medium">{group.name}</p>
                  {group.description && (
                    <p className="text-sm text-muted-foreground">{group.description}</p>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground">
                    {group.device_count.toLocaleString()}
                  </span>{" "}
                  {group.device_count === 1 ? "device" : "devices"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
