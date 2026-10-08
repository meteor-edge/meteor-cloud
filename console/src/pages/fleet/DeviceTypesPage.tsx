import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { createDeviceType, listDeviceTypes } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { DevicesNav } from "@/components/fleet/DevicesNav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageDeviceTypes } from "@/lib/permissions";

export function DeviceTypesPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [model, setModel] = useState("");
  const [architecture, setArchitecture] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  const canManage = canManageDeviceTypes(orgQuery.data?.current_user_role);

  const createMutation = useMutation({
    mutationFn: () =>
      createDeviceType(token!, organizationId, {
        name,
        manufacturer: manufacturer || undefined,
        model: model || undefined,
        architecture: architecture || undefined,
        description: description || undefined,
      }),
    onSuccess: async () => {
      setName("");
      setManufacturer("");
      setModel("");
      setArchitecture("");
      setDescription("");
      setError(null);
      setShowForm(false);
      await queryClient.invalidateQueries({ queryKey: ["device-types", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create device type.");
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  const types = typesQuery.data ?? [];

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Device Types</h1>
          <p className="mt-2 text-muted-foreground">
            The hardware models in your fleet. Each type holds its OS images and other artifacts.
          </p>
        </div>
        {canManage && (
          <Button
            onClick={() => {
              setError(null);
              setShowForm((open) => !open);
            }}
          >
            {showForm ? "Close" : "Add device type"}
          </Button>
        )}
      </div>
      <DevicesNav organizationId={organizationId} />

      {canManage && showForm && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow md:grid-cols-2"
          onSubmit={onCreate}
        >
          <div>
            <Label htmlFor="type-name">Name</Label>
            <Input
              id="type-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Raspberry Pi 4"
              required
            />
          </div>
          <div>
            <Label htmlFor="type-manufacturer">Manufacturer</Label>
            <Input
              id="type-manufacturer"
              value={manufacturer}
              onChange={(event) => setManufacturer(event.target.value)}
              placeholder="e.g. Raspberry Pi Ltd"
            />
          </div>
          <div>
            <Label htmlFor="type-model">Model</Label>
            <Input
              id="type-model"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="e.g. 4 Model B"
            />
          </div>
          <div>
            <Label htmlFor="type-architecture">Architecture</Label>
            <Input
              id="type-architecture"
              value={architecture}
              onChange={(event) => setArchitecture(event.target.value)}
              placeholder="e.g. arm64"
            />
          </div>
          <div className="md:col-span-2">
            <Label htmlFor="type-description">Description</Label>
            <Input
              id="type-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Create device type
            </Button>
          </div>
        </form>
      )}

      {error && (
        <p className="text-sm font-medium text-midnight bg-ember rounded-md px-2 py-1">{error}</p>
      )}

      {typesQuery.isLoading ? (
        <p className="text-muted-foreground">Loading device types…</p>
      ) : types.length === 0 ? (
        <p className="text-muted-foreground">
          No device types yet. Add one for each kind of hardware you run, e.g. Raspberry Pi 4.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-semibold">Name</th>
                <th className="px-4 py-3 font-semibold">Manufacturer / Model</th>
                <th className="px-4 py-3 font-semibold">Architecture</th>
                <th className="px-4 py-3 font-semibold">Devices</th>
                <th className="px-4 py-3 font-semibold">Artifacts</th>
              </tr>
            </thead>
            <tbody>
              {types.map((type) => (
                <tr key={type.id} className="border-b border-border">
                  <td className="px-4 py-3">
                    <Link
                      to={`/organizations/${organizationId}/device-types/${type.id}`}
                      className="font-medium text-foreground hover:text-link"
                    >
                      {type.name}
                    </Link>
                    {type.description && (
                      <p className="text-xs text-muted-foreground">{type.description}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {[type.manufacturer, type.model].filter(Boolean).join(" · ") || "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{type.architecture ?? "—"}</td>
                  <td className="px-4 py-3">{type.device_count}</td>
                  <td className="px-4 py-3">
                    <Link
                      to={`/organizations/${organizationId}/device-types/${type.id}?tab=artifacts`}
                      className="text-link hover:underline"
                    >
                      {type.artifact_count}
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
