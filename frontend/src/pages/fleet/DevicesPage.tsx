import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  createRegistrationToken,
  deleteDevice,
  listDeviceGroups,
  listDeviceTypes,
  listDevices,
  listRegistrationTokens,
  revokeRegistrationToken,
  type ConnectivityStatus,
  type DeviceListParams,
  type RegistrationTokenWithSecret,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { FleetNav } from "@/components/fleet/FleetNav";
import { OneTimeSecretDialog } from "@/components/fleet/OneTimeSecretDialog";
import { PendingEnrollmentRequests } from "@/components/fleet/PendingEnrollmentRequests";
import { StatusBadge } from "@/components/fleet/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageFleet } from "@/lib/permissions";
import { cn, formatDateTime } from "@/lib/utils";

const PAGE_SIZE = 10;

function buildRegisterCommand(serverOrigin: string, token: string): string {
  return [
    "meteorcli register \\",
    `  --server ${serverOrigin} \\`,
    `  --token ${token}`,
  ].join("\n");
}

export function DevicesPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<ConnectivityStatus | "">("");
  const [typeId, setTypeId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [sort, setSort] = useState<DeviceListParams["sort"]>("name");
  const [order, setOrder] = useState<DeviceListParams["order"]>("asc");
  const [page, setPage] = useState(1);

  // Add-device flow: define type + group, mint a one-time registration token.
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTypeId, setNewTypeId] = useState("");
  const [newGroupId, setNewGroupId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  // The plaintext token only ever lives in local UI state after creation.
  const [createdToken, setCreatedToken] = useState<RegistrationTokenWithSecret | null>(null);

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
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const tokensQuery = useQuery({
    queryKey: ["registration-tokens", organizationId, token],
    queryFn: () => listRegistrationTokens(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  const typeNames = useMemo(() => {
    const map = new Map<string, string>();
    (typesQuery.data ?? []).forEach((type) => map.set(type.id, type.name));
    return map;
  }, [typesQuery.data]);
  const groupNames = useMemo(() => {
    const map = new Map<string, string>();
    (groupsQuery.data ?? []).forEach((group) => map.set(group.id, group.name));
    return map;
  }, [groupsQuery.data]);

  // A token that has never been used and is not revoked represents a device that
  // has been defined but has not checked in yet.
  const pendingTokens = useMemo(
    () =>
      (tokensQuery.data ?? []).filter(
        (entry) => entry.revoked_at === null && entry.use_count === 0,
      ),
    [tokensQuery.data],
  );

  const params: DeviceListParams = {
    search: search || undefined,
    status: status || undefined,
    device_type_id: typeId || undefined,
    device_group_id: groupId || undefined,
    sort,
    order,
    page,
    page_size: PAGE_SIZE,
  };

  const devicesQuery = useQuery({
    queryKey: ["devices", organizationId, params, token],
    queryFn: () => listDevices(token!, organizationId, params),
    enabled: Boolean(token && organizationId),
  });

  const total = devicesQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const invalidateTokens = () =>
    queryClient.invalidateQueries({ queryKey: ["registration-tokens", organizationId] });

  const isRefreshing = devicesQuery.isFetching || tokensQuery.isFetching;

  async function refreshFleet() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["devices", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["registration-tokens", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["enrollment-requests", organizationId] }),
    ]);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createRegistrationToken(token!, organizationId, {
        name: newName,
        // One token defines a single device by default.
        max_uses: 1,
        device_type_id: newTypeId || undefined,
        device_group_id: newGroupId || undefined,
      }),
    onSuccess: async (created) => {
      setCreatedToken(created);
      setNewName("");
      setNewTypeId("");
      setNewGroupId("");
      setShowAddForm(false);
      setFormError(null);
      await invalidateTokens();
    },
    onError: (err: unknown) => {
      setFormError(err instanceof ApiError ? err.message : "Could not create the device token.");
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  async function onRevoke(tokenId: string) {
    if (!window.confirm("Cancel this pending device registration?")) {
      return;
    }
    setFormError(null);
    try {
      await revokeRegistrationToken(token!, organizationId, tokenId);
      await invalidateTokens();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not cancel the registration.");
    }
  }

  async function onDeleteDevice(deviceId: string, deviceName: string) {
    if (!window.confirm(`Delete device "${deviceName}"? This cannot be undone.`)) {
      return;
    }
    setFormError(null);
    try {
      await deleteDevice(token!, organizationId, deviceId);
      await queryClient.invalidateQueries({ queryKey: ["devices", organizationId] });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not delete the device.");
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Fleet</h1>
          <p className="mt-2 text-muted-foreground">{orgQuery.data?.name}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-10 w-10 rounded-full p-0"
            aria-label="Refresh devices"
            disabled={isRefreshing}
            onClick={() => void refreshFleet()}
          >
            <RefreshCw className={cn("h-4 w-4", isRefreshing && "animate-spin")} />
          </Button>
          {canManage && (
            <Button
              onClick={() => {
                setFormError(null);
                setShowAddForm((open) => !open);
              }}
            >
              {showAddForm ? "Close" : "Add device"}
            </Button>
          )}
        </div>
      </div>
      <FleetNav organizationId={organizationId} />

      {canManage && showAddForm && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-white/80 p-5 shadow-sm md:grid-cols-2"
          onSubmit={onCreate}
        >
          <div className="md:col-span-2">
            <p className="text-sm font-medium text-foreground">Define a device</p>
            <p className="text-xs text-muted-foreground">
              Choose a type and group, then create a registration token. The device appears below
              and comes online once the agent registers with the token.
            </p>
          </div>
          <div>
            <Label htmlFor="device-name">Name</Label>
            <Input
              id="device-name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="e.g. edge-gateway-01"
              required
            />
          </div>
          <div>
            <Label htmlFor="device-type">Device type</Label>
            <select
              id="device-type"
              className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
              value={newTypeId}
              onChange={(event) => setNewTypeId(event.target.value)}
            >
              <option value="">None</option>
              {(typesQuery.data ?? []).map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="device-group">Device group</Label>
            <select
              id="device-group"
              className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
              value={newGroupId}
              onChange={(event) => setNewGroupId(event.target.value)}
            >
              <option value="">None</option>
              {(groupsQuery.data ?? []).map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Create token
            </Button>
          </div>
        </form>
      )}

      {formError && <p className="text-sm text-red-700">{formError}</p>}

      {token && (
        <PendingEnrollmentRequests
          organizationId={organizationId}
          token={token}
          canManage={canManage}
          hideWhenEmpty
          onError={setFormError}
        />
      )}

      {pendingTokens.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Pending registrations
          </h2>
          <div className="overflow-hidden rounded-lg border border-border bg-white/80 shadow-sm">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-3 font-semibold">Name</th>
                  <th className="px-4 py-3 font-semibold">Type</th>
                  <th className="px-4 py-3 font-semibold">Group</th>
                  <th className="px-4 py-3 font-semibold">Token</th>
                  <th className="px-4 py-3 font-semibold">Created</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  {canManage && <th className="px-4 py-3 font-semibold">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {pendingTokens.map((entry) => (
                  <tr key={entry.id} className="border-b border-border/70">
                    <td className="px-4 py-3 font-medium">{entry.name}</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {entry.device_type_id ? typeNames.get(entry.device_type_id) ?? "—" : "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {entry.device_group_id ? groupNames.get(entry.device_group_id) ?? "—" : "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{entry.token_prefix}…</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {formatDateTime(entry.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                        Awaiting registration
                      </span>
                    </td>
                    {canManage && (
                      <td className="px-4 py-3">
                        <Button variant="ghost" size="sm" onClick={() => onRevoke(entry.id)}>
                          Cancel
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid gap-3 rounded-lg border border-border bg-white/80 p-4 shadow-sm md:grid-cols-4">
        <Input
          aria-label="Search devices"
          placeholder="Search name, hostname, serial, MAC…"
          value={search}
          onChange={(event) => {
            setPage(1);
            setSearch(event.target.value);
          }}
        />
        <select
          aria-label="Filter by status"
          className="h-10 rounded-md border border-input bg-white px-3 text-sm"
          value={status}
          onChange={(event) => {
            setPage(1);
            setStatus(event.target.value as ConnectivityStatus | "");
          }}
        >
          <option value="">All statuses</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="never_seen">Never seen</option>
        </select>
        <select
          aria-label="Filter by device type"
          className="h-10 rounded-md border border-input bg-white px-3 text-sm"
          value={typeId}
          onChange={(event) => {
            setPage(1);
            setTypeId(event.target.value);
          }}
        >
          <option value="">All types</option>
          {(typesQuery.data ?? []).map((type) => (
            <option key={type.id} value={type.id}>
              {type.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by device group"
          className="h-10 rounded-md border border-input bg-white px-3 text-sm"
          value={groupId}
          onChange={(event) => {
            setPage(1);
            setGroupId(event.target.value);
          }}
        >
          <option value="">All groups</option>
          {(groupsQuery.data ?? []).map((group) => (
            <option key={group.id} value={group.id}>
              {group.name}
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-white/80 shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() => {
                    setSort("name");
                    setOrder(order === "asc" ? "desc" : "asc");
                  }}
                >
                  Name {sort === "name" ? (order === "asc" ? "↑" : "↓") : ""}
                </button>
              </th>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Architecture</th>
              <th className="px-4 py-3 font-semibold">
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() => {
                    setSort("last_seen_at");
                    setOrder(order === "asc" ? "desc" : "asc");
                  }}
                >
                  Last seen {sort === "last_seen_at" ? (order === "asc" ? "↑" : "↓") : ""}
                </button>
              </th>
              <th className="px-4 py-3 font-semibold">Registered</th>
              <th className="px-4 py-3 font-semibold">Enabled</th>
              {canManage && <th className="px-4 py-3 font-semibold">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {devicesQuery.isLoading && (
              <tr>
                <td
                  className="px-4 py-6 text-center text-muted-foreground"
                  colSpan={canManage ? 7 : 6}
                >
                  Loading devices…
                </td>
              </tr>
            )}
            {!devicesQuery.isLoading &&
              (devicesQuery.data?.items ?? []).map((device) => (
                <tr key={device.id} className="border-b border-border/70">
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/organizations/${organizationId}/devices/${device.id}`}
                      className="text-foreground hover:text-primary"
                    >
                      {device.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={device.status} />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {device.architecture ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {device.last_seen_at
                      ? formatDateTime(device.last_seen_at)
                      : "Never"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDateTime(device.registered_at ?? device.created_at)}
                  </td>
                  <td className="px-4 py-3">{device.is_enabled ? "Yes" : "No"}</td>
                  {canManage && (
                    <td className="px-4 py-3">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-700"
                        onClick={() => onDeleteDevice(device.id, device.name)}
                      >
                        Delete
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            {!devicesQuery.isLoading && (devicesQuery.data?.items ?? []).length === 0 && (
              <tr>
                <td
                  className="px-4 py-6 text-center text-muted-foreground"
                  colSpan={canManage ? 7 : 6}
                >
                  No devices found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{total} device(s)</p>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
          >
            Next
          </Button>
        </div>
      </div>

      {createdToken && (
        <OneTimeSecretDialog
          title="Device token created"
          description="Copy this token now and register the device with it. For security it will not be shown again."
          secret={createdToken.token}
          command={buildRegisterCommand(window.location.origin, createdToken.token)}
          onClose={() => setCreatedToken(null)}
        />
      )}
    </section>
  );
}
