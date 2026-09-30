import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import {
  createDeviceType,
  deleteDeviceType,
  listDeviceTypes,
  updateDeviceType,
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

export function DeviceTypesPage() {
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

  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["device-types", organizationId] });

  const createMutation = useMutation({
    mutationFn: () =>
      createDeviceType(token!, organizationId, {
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
      setError(err instanceof ApiError ? err.message : "Could not create device type.");
    },
  });

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  async function onSaveEdit(typeId: string) {
    setError(null);
    try {
      await updateDeviceType(token!, organizationId, typeId, { name: editName });
      setEditingId(null);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update device type.");
    }
  }

  async function onDelete(typeId: string) {
    if (!window.confirm("Delete this device type?")) {
      return;
    }
    setError(null);
    try {
      await deleteDeviceType(token!, organizationId, typeId);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete device type.");
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
            <Label htmlFor="type-name">Name</Label>
            <Input
              id="type-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
          <div>
            <Label htmlFor="type-description">Description</Label>
            <Input
              id="type-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Add type
            </Button>
          </div>
        </form>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}

      {typesQuery.isLoading ? (
        <p className="text-muted-foreground">Loading device types…</p>
      ) : (typesQuery.data ?? []).length === 0 ? (
        <p className="text-muted-foreground">No device types yet.</p>
      ) : (
        <ul className="space-y-3">
          {(typesQuery.data ?? []).map((type) => (
            <li
              key={type.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-white/80 p-4 shadow-sm"
            >
              {editingId === type.id ? (
                <div className="flex flex-1 items-center gap-2">
                  <Input value={editName} onChange={(event) => setEditName(event.target.value)} />
                  <Button size="sm" onClick={() => onSaveEdit(type.id)}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div>
                  <p className="font-medium">{type.name}</p>
                  {type.description && (
                    <p className="text-sm text-muted-foreground">{type.description}</p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">
                    Created {formatDateTime(type.created_at)}
                  </p>
                </div>
              )}
              {canManage && editingId !== type.id && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditingId(type.id);
                      setEditName(type.name);
                    }}
                  >
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDelete(type.id)}>
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
