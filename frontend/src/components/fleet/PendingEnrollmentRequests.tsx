import { FormEvent, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveEnrollmentRequest,
  listDeviceGroups,
  listDeviceTypes,
  listEnrollmentRequests,
  rejectEnrollmentRequest,
  type DeviceEnrollmentRequest,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/utils";

function isActiveEnrollmentRequest(entry: DeviceEnrollmentRequest): boolean {
  return entry.status === "pending" || (entry.status === "approved" && entry.claimed_at == null);
}

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
  const [approveTypeId, setApproveTypeId] = useState("");
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
        name: approveName || undefined,
        device_type_id: approveTypeId || undefined,
        device_group_id: approveGroupId || undefined,
      });
      setApproveId(null);
      setApproveName("");
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
      <div className="overflow-hidden rounded-lg border border-border bg-white/80 shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Hostname</th>
              <th className="px-4 py-3 font-semibold">MAC address</th>
              <th className="px-4 py-3 font-semibold">Architecture</th>
              <th className="px-4 py-3 font-semibold">Requested</th>
              <th className="px-4 py-3 font-semibold">Status</th>
              {canManage && <th className="px-4 py-3 font-semibold">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {activeRequests.map((entry) => (
              <tr key={entry.id} className="border-b border-border/70">
                <td className="px-4 py-3 font-medium">
                  {entry.assigned_name ?? entry.requested_name ?? "—"}
                </td>
                <td className="px-4 py-3 text-muted-foreground">{entry.hostname ?? "—"}</td>
                <td className="px-4 py-3 font-mono text-xs">
                  {entry.mac_addresses.length ? entry.mac_addresses.join(", ") : "—"}
                </td>
                <td className="px-4 py-3 text-muted-foreground">{entry.architecture ?? "—"}</td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDateTime(entry.created_at)}
                </td>
                <td className="px-4 py-3">
                  {entry.status === "approved" ? (
                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                      Awaiting device
                    </span>
                  ) : (
                    <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800">
                      Pending approval
                    </span>
                  )}
                </td>
                {canManage && (
                  <td className="px-4 py-3">
                    {entry.status === "pending" ? (
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          onClick={() => {
                            setApproveId(entry.id);
                            setApproveName(entry.requested_name ?? "");
                            setApproveTypeId(entry.device_type_id ?? "");
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
                      <span className="text-xs text-muted-foreground">Waiting for claim</span>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {activeRequests.length === 0 && (
              <tr>
                <td
                  className="px-4 py-6 text-center text-muted-foreground"
                  colSpan={canManage ? 7 : 6}
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
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Approve device"
        >
          <form
            className="grid w-full max-w-lg gap-3 rounded-lg border border-border bg-white p-6 shadow-lg"
            onSubmit={onApprove}
          >
            <div>
              <h2 className="text-lg font-semibold">Approve device</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Optionally set the name, type, and group assigned when the device claims its
                credential.
              </p>
            </div>
            <div>
              <Label htmlFor="approve-name">Name</Label>
              <Input
                id="approve-name"
                value={approveName}
                onChange={(event) => setApproveName(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="approve-type">Device type</Label>
              <select
                id="approve-type"
                className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
                value={approveTypeId}
                onChange={(event) => setApproveTypeId(event.target.value)}
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
                className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
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
