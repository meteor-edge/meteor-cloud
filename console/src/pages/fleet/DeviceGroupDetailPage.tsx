import { FormEvent, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  deleteDeviceGroup,
  getDeviceGroup,
  listDeviceTypes,
  updateDeviceGroup,
  type DeviceGroup,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { DeviceListTable } from "@/components/fleet/DeviceListTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs } from "@/components/ui/tabs";
import { canManageDeviceGroups } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

type Tab = "overview" | "devices";

export function DeviceGroupDetailPage() {
  const { organizationId = "", groupId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const tab: Tab = searchParams.get("tab") === "devices" ? "devices" : "overview";

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const groupQuery = useQuery({
    queryKey: ["device-group", organizationId, groupId, token],
    queryFn: () => getDeviceGroup(token!, organizationId, groupId),
    enabled: Boolean(token && organizationId && groupId),
  });
  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageDeviceGroups(orgQuery.data?.current_user_role);
  const typeNames = useMemo(
    () => new Map((typesQuery.data ?? []).map((type) => [type.id, type.name])),
    [typesQuery.data],
  );

  if (groupQuery.isLoading) {
    return <p className="text-muted-foreground">Loading device group…</p>;
  }
  if (!groupQuery.data) {
    return (
      <p className="rounded-md bg-ember px-2 py-1 text-midnight">Device group was not found.</p>
    );
  }
  const group = groupQuery.data;

  async function onDelete() {
    if (!window.confirm(`Delete device group "${group.name}"?`)) {
      return;
    }
    setError(null);
    try {
      await deleteDeviceGroup(token!, organizationId, groupId);
      await queryClient.invalidateQueries({ queryKey: ["device-groups", organizationId] });
      navigate(`/organizations/${organizationId}/device-groups`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete device group.");
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">Device Group</p>
          <h1 className="text-3xl font-semibold tracking-tight">{group.name}</h1>
          <p className="mt-2 text-muted-foreground">
            {group.device_count.toLocaleString()} {group.device_count === 1 ? "device" : "devices"}
          </p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/device-groups`}>Back to device groups</Link>
        </Button>
      </div>

      <Tabs
        label="Device group sections"
        active={tab}
        onChange={(next) =>
          setSearchParams(next === "overview" ? {} : { tab: next }, { replace: true })
        }
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "devices", label: "Devices", count: group.device_count },
        ]}
      />

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      {tab === "overview" && (
        <DeviceGroupOverview
          key={group.updated_at}
          group={group}
          canManage={canManage}
          token={token!}
          organizationId={organizationId}
          onError={setError}
          onDelete={onDelete}
          onSaved={() =>
            Promise.all([
              queryClient.invalidateQueries({
                queryKey: ["device-group", organizationId, groupId],
              }),
              queryClient.invalidateQueries({ queryKey: ["device-groups", organizationId] }),
            ])
          }
        />
      )}

      {tab === "devices" && token && (
        <DeviceListTable
          token={token}
          organizationId={organizationId}
          filter={{ device_group_id: groupId }}
          secondaryColumn={{ label: "Device Type", names: typeNames, key: "device_type_id" }}
        />
      )}
    </section>
  );
}

function DeviceGroupOverview({
  group,
  canManage,
  token,
  organizationId,
  onSaved,
  onDelete,
  onError,
}: {
  group: DeviceGroup;
  canManage: boolean;
  token: string;
  organizationId: string;
  onSaved: () => Promise<unknown>;
  onDelete: () => void;
  onError: (message: string | null) => void;
}) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    onError(null);
    setSaving(true);
    try {
      await updateDeviceGroup(token, organizationId, group.id, {
        name,
        description: description || null,
      });
      await onSaved();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not update device group.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 rounded-lg border border-border bg-card p-6 shadow-glow sm:grid-cols-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Devices
          </p>
          <p className="mt-1 text-sm">{group.device_count}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Slug
          </p>
          <p className="mt-1 text-sm">{group.slug}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Created
          </p>
          <p className="mt-1 text-sm">{formatDateTime(group.created_at)}</p>
        </div>
        <div className="sm:col-span-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Description
          </p>
          <p className="mt-1 text-sm">{group.description || "—"}</p>
        </div>
      </div>

      {canManage && (
        <form
          className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
          onSubmit={onSubmit}
        >
          <h2 className="text-lg font-semibold">Edit device group</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="edit-group-name">Name</Label>
              <Input
                id="edit-group-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div>
              <Label htmlFor="edit-group-description">Description</Label>
              <Input
                id="edit-group-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <Button type="submit" disabled={saving}>
              Save changes
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="bg-ember text-midnight hover:bg-ember hover:text-midnight"
              onClick={onDelete}
            >
              Delete device group
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
