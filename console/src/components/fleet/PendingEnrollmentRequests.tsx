import { FormEvent, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveEnrollmentRequest,
  listDeviceGroups,
  listDeviceTypes,
  listDevices,
  listEnrollmentRequests,
  rejectEnrollmentRequest,
  type DeviceEnrollmentRequest,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { typedDefaultName } from "@/lib/deviceNames";
import { useDebounced } from "@/lib/useDebounced";
import { formatDateTime } from "@/lib/utils";

function isActiveEnrollmentRequest(entry: DeviceEnrollmentRequest): boolean {
  return entry.status === "pending" || (entry.status === "approved" && entry.claimed_at == null);
}

function displayName(entry: DeviceEnrollmentRequest): string {
  return entry.assigned_name ?? entry.suggested_name ?? entry.requested_name ?? "—";
}

function primaryMac(addresses: string[]): string | null {
  return addresses[0] ?? null;
}

function enrollmentKeyLabel(entry: DeviceEnrollmentRequest): string {
  return entry.enrollment_key_name || entry.enrollment_key_prefix || "Revoked/deleted key";
}

function sameName(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

const NAME_CHECK_DELAY_MS = 400;

type PendingEnrollmentRequestsProps = {
  organizationId: string;
  token: string;
  canManage: boolean;
  hideWhenEmpty?: boolean;
  onError?: (message: string) => void;
};

export function PendingEnrollmentRequests({
  organizationId,
  token,
  canManage,
  hideWhenEmpty = false,
  onError,
}: PendingEnrollmentRequestsProps) {
  const queryClient = useQueryClient();
  const [approveId, setApproveId] = useState<string | null>(null);
  const [approveName, setApproveName] = useState("");
  const [approveDefaultName, setApproveDefaultName] = useState("");
  const [approveTypeId, setApproveTypeId] = useState("");
  const [approveSuggestedName, setApproveSuggestedName] = useState("");
  const [approveGroupId, setApproveGroupId] = useState("");

  const requestsQuery = useQuery({
    queryKey: ["enrollment-requests", organizationId, token],
    queryFn: () => listEnrollmentRequests(token, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const activeRequests = useMemo(
    () => (requestsQuery.data ?? []).filter(isActiveEnrollmentRequest),
    [requestsQuery.data],
  );

  // Repeated names are allowed, so this only warns. A device keeps its suggested name without a check.
  const typedName = useDebounced(approveName.trim(), NAME_CHECK_DELAY_MS);
  const checkName = Boolean(approveId && typedName && typedName !== approveDefaultName);
  const namesakesQuery = useQuery({
    queryKey: ["devices", organizationId, "name", typedName, token],
    queryFn: () =>
      listDevices(token, organizationId, { name: typedName, page_size: 1 }),
    enabled: checkName,
  });
  const nameInUse =
    checkName &&
    ((namesakesQuery.data?.total ?? 0) > 0 ||
      activeRequests.some(
        (entry) => entry.id !== approveId && sameName(displayName(entry), typedName),
      ));

  /** A generated name follows the chosen type until someone types their own. */
  function onApproveTypeChange(typeId: string) {
    setApproveTypeId(typeId);
    const entry = activeRequests.find((item) => item.id === approveId);
    if (!entry?.name_suffix || approveName !== approveDefaultName) {
      return;
    }
    const type = (typesQuery.data ?? []).find((item) => item.id === typeId);
    const name =
      (type && typedDefaultName(type.slug, entry.name_suffix)) || approveSuggestedName;
    setApproveName(name);
    setApproveDefaultName(name);
  }

  if (hideWhenEmpty && activeRequests.length === 0) {
    return null;
  }

  async function onApprove(event: FormEvent) {
    event.preventDefault();
    if (!approveId) {
      return;
    }
    try {
      await approveEnrollmentRequest(token, organizationId, approveId, {
        name: approveName.trim() || undefined,
        device_type_id: approveTypeId || undefined,
        device_group_id: approveGroupId || undefined,
      });
      setApproveId(null);
      setApproveName("");
      setApproveDefaultName("");
      setApproveTypeId("");
      setApproveGroupId("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["enrollment-requests", organizationId] }),
        queryClient.invalidateQueries({ queryKey: ["devices", organizationId] }),
      ]);
    } catch (err) {
      onError?.(err instanceof ApiError ? err.message : "Could not approve the request.");
    }
  }

  async function onReject(request: DeviceEnrollmentRequest) {
    const reason = window.prompt("Optional rejection reason:", "") ?? undefined;
    try {
      await rejectEnrollmentRequest(token, organizationId, request.id, {
        reason: reason || undefined,
      });
      await queryClient.invalidateQueries({
        queryKey: ["enrollment-requests", organizationId],
      });
    } catch (err) {
      onError?.(err instanceof ApiError ? err.message : "Could not reject the request.");
    }
  }

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Pending enrollment requests</h2>
      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Hostname</th>
              <th className="px-4 py-3 font-semibold">MAC address</th>
              <th className="px-4 py-3 font-semibold">Enrollment key</th>
              <th className="px-4 py-3 font-semibold">Architecture</th>
              <th className="px-4 py-3 font-semibold">Requested</th>
              <th className="px-4 py-3 font-semibold whitespace-nowrap">Status</th>
              {canManage && <th className="px-4 py-3 font-semibold whitespace-nowrap">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {activeRequests.map((entry) => (
              <tr key={entry.id} className="border-b border-border">
                <td className="px-4 py-3 font-medium">
                  {displayName(entry)}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {entry.hostname ?? "—"}
                  {entry.hardware_model && (
                    <span className="block text-xs">{entry.hardware_model}</span>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{primaryMac(entry.mac_addresses) ?? "—"}</td>
                <td className="px-4 py-3 text-muted-foreground">{enrollmentKeyLabel(entry)}</td>
                <td className="px-4 py-3 text-muted-foreground">{entry.architecture ?? "—"}</td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDateTime(entry.created_at)}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  {entry.status === "approved" ? (
                    <span className="inline-flex whitespace-nowrap rounded-full bg-ember px-2 py-0.5 text-xs font-medium text-midnight">
                      Awaiting device
                    </span>
                  ) : (
                    <span className="inline-flex whitespace-nowrap rounded-full bg-sky-blue px-2 py-0.5 text-xs font-medium text-midnight">
                      Pending approval
                    </span>
                  )}
                </td>
                {canManage && (
                  <td className="px-4 py-3 whitespace-nowrap">
                    {entry.status === "pending" ? (
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          onClick={() => {
                            setApproveId(entry.id);
                            const name = entry.suggested_name ?? entry.requested_name ?? "";
                            setApproveName(name);
                            setApproveDefaultName(name);
                            setApproveSuggestedName(name);
                            setApproveTypeId(
                              entry.device_type_id ?? entry.suggested_device_type_id ?? "",
                            );
                            setApproveGroupId(entry.device_group_id ?? "");
                          }}
                        >
                          Approve
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => onReject(entry)}>
                          Reject
                        </Button>
                      </div>
                    ) : (
                      <span className="whitespace-nowrap text-xs text-muted-foreground">
                        Waiting for claim
                      </span>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {activeRequests.length === 0 && (
              <tr>
                <td
                  className="px-4 py-6 text-center text-muted-foreground"
                  colSpan={canManage ? 8 : 7}
                >
                  No pending enrollment requests.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canManage && approveId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Approve device"
        >
          <form
            className="grid w-full max-w-lg gap-3 rounded-lg border border-border bg-card p-6 shadow-glow"
            onSubmit={onApprove}
          >
            <div>
              <h2 className="text-lg font-semibold">Approve device</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The device gets this name, type, and group when it claims its credential.
              </p>
            </div>
            <div>
              <Label htmlFor="approve-name">Name</Label>
              <Input
                id="approve-name"
                value={approveName}
                onChange={(event) => setApproveName(event.target.value)}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                {approveName === approveDefaultName &&
                activeRequests.find((item) => item.id === approveId)?.name_suffix
                  ? "Device type and the end of its MAC address. It follows the device type below; you can rename it later."
                  : "You can rename it later."}
              </p>
              {nameInUse && (
                <p className="mt-1 text-xs text-destructive">
                  Another device already has this name. You can still use it.
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="approve-type">Device type</Label>
              <select
                id="approve-type"
                className="flex h-10 w-full rounded-md border border-input bg-field text-foreground px-3 text-sm"
                value={approveTypeId}
                onChange={(event) => onApproveTypeChange(event.target.value)}
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
              <Label htmlFor="approve-group">Device group</Label>
              <select
                id="approve-group"
                className="flex h-10 w-full rounded-md border border-input bg-field text-foreground px-3 text-sm"
                value={approveGroupId}
                onChange={(event) => setApproveGroupId(event.target.value)}
              >
                <option value="">None</option>
                {(groupsQuery.data ?? []).map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="secondary" onClick={() => setApproveId(null)}>
                Cancel
              </Button>
              <Button type="submit">Confirm approval</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
