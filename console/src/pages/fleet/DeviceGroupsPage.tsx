import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import {
  createDeviceGroup,
  deleteDeviceGroup,
  listDeviceGroups,
  updateDeviceGroup,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { FleetNav } from "@/components/fleet/FleetNav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageFleet } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export function DeviceGroupsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

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

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["device-groups", organizationId] });

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
      await invalidate();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create device group.");
    },
  });

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  async function onSaveEdit(groupId: string) {
    setError(null);
    try {
      await updateDeviceGroup(token!, organizationId, groupId, { name: editName });
      setEditingId(null);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update device group.");
    }
  }

  async function onDelete(groupId: string) {
    if (!window.confirm("Delete this device group?")) {
      return;
    }
    setError(null);
    try {
      await deleteDeviceGroup(token!, organizationId, groupId);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete device group.");
    }
  }

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Fleet</h1>
        <p className="mt-2 text-muted-foreground">{orgQuery.data?.name}</p>
      </div>
      <FleetNav organizationId={organizationId} />

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-white/80 p-5 shadow-sm md:grid-cols-[1fr_1fr_auto]"
          onSubmit={onCreate}
        >
          <div>
            <Label htmlFor="group-name">Name</Label>
            <Input
              id="group-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
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

      {error && <p className="text-sm text-red-700">{error}</p>}

      {groupsQuery.isLoading ? (
        <p className="text-muted-foreground">Loading device groups…</p>
      ) : (groupsQuery.data ?? []).length === 0 ? (
        <p className="text-muted-foreground">No device groups yet.</p>
      ) : (
        <ul className="space-y-3">
          {(groupsQuery.data ?? []).map((group) => (
            <li
              key={group.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-white/80 p-4 shadow-sm"
            >
              {editingId === group.id ? (
                <div className="flex flex-1 items-center gap-2">
                  <Input value={editName} onChange={(event) => setEditName(event.target.value)} />
                  <Button size="sm" onClick={() => onSaveEdit(group.id)}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div>
                  <p className="font-medium">{group.name}</p>
                  {group.description && (
                    <p className="text-sm text-muted-foreground">{group.description}</p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">
                    Created {formatDateTime(group.created_at)}
                  </p>
                </div>
              )}
              {canManage && editingId !== group.id && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditingId(group.id);
                      setEditName(group.name);
                    }}
                  >
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDelete(group.id)}>
                    Delete
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
