import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getMyAccess } from "@/api/access";
import {
  getDevice,
  getDeviceSoftware,
  listSoftwareActions,
  orderSoftwareAction,
  type LogSource,
  type SoftwareAction,
  type SoftwareActionKind,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { DeviceLogs } from "@/components/fleet/DeviceLogs";
import { SoftwareHealth } from "@/components/fleet/SoftwareHealth";
import { SoftwareOverview } from "@/components/fleet/SoftwareOverview";
import { HealthBadge, StoppedBadge } from "@/components/fleet/StatusBadge";
import { Button } from "@/components/ui/button";
import { Tabs, type TabItem } from "@/components/ui/tabs";
import { canViewDeviceLogs } from "@/lib/permissions";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";

type Tab = "overview" | "health" | "logs" | "activity";

const ACTION_LABELS: Record<SoftwareActionKind, string> = {
  workload_restart: "Restart",
  workload_stop: "Stop",
  workload_start: "Start",
  workload_snapshot: "Refresh",
};

const STATE_LABELS: Record<SoftwareAction["state"], string> = {
  pending: "Waiting for the device",
  sent: "Running",
  done: "Done",
  failed: "Failed",
};

/** One workload on one device: what runs, its health over time, logs, and restart / stop / start. */
export function SoftwareDetailPage() {
  const { organizationId = "", deviceId = "", softwareId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const ready = Boolean(token && organizationId && deviceId && softwareId);

  const softwareKey = ["device-software-detail", organizationId, deviceId, softwareId, token];
  const actionsKey = ["software-actions", organizationId, deviceId, softwareId, token];
  const actionsQuery = useQuery({
    queryKey: actionsKey,
    queryFn: () => listSoftwareActions(token!, organizationId, deviceId, softwareId),
    enabled: ready,
    refetchInterval: (query) =>
      query.state.data?.some((a) => a.state === "pending" || a.state === "sent") ? 2_000 : 15_000,
  });
  const actions = actionsQuery.data ?? [];
  const waiting = actions.filter((a) => a.state === "pending" || a.state === "sent");
  const softwareQuery = useQuery({
    queryKey: softwareKey,
    queryFn: () => getDeviceSoftware(token!, organizationId, deviceId, softwareId),
    enabled: ready,
    refetchInterval: waiting.length > 0 ? 2_000 : 10_000,
  });
  const deviceQuery = useQuery({
    queryKey: ["device", organizationId, deviceId, token],
    queryFn: () => getDevice(token!, organizationId, deviceId),
    enabled: ready,
    refetchInterval: 15_000,
  });
  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: ready,
  });
  const accessQuery = useQuery({
    queryKey: ["my-access", token],
    queryFn: () => getMyAccess(token!),
    enabled: Boolean(token),
    retry: false,
  });
  const order = useMutation({
    mutationFn: (payload: { kind: SoftwareActionKind; service?: string }) =>
      orderSoftwareAction(token!, organizationId, deviceId, softwareId, payload),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: actionsKey });
      void queryClient.invalidateQueries({ queryKey: softwareKey });
    },
  });

  const deviceHref = `/organizations/${organizationId}/devices/${deviceId}`;
  const software = softwareQuery.data;
  const device = deviceQuery.data;
  if (!software) {
    return (
      <div className="space-y-4">
        <Link to={deviceHref} className="text-sm text-link hover:underline">
          ← Device
        </Link>
        <p className="text-sm text-muted-foreground">
          {softwareQuery.isError
            ? softwareQuery.error instanceof ApiError
              ? softwareQuery.error.message
              : "Could not load this software."
            : "Loading software…"}
        </p>
      </div>
    );
  }

  const orgAccess = accessQuery.data?.organizations.find(
    (entry) => entry.organization_id === organizationId,
  );
  const canLogs = orgAccess
    ? orgAccess.permissions.includes("device.logs")
    : canViewDeviceLogs(orgQuery.data?.current_user_role);
  const online = device?.status === "online";
  const snapshot = software.snapshot;
  // Without a snapshot the device has not said it would refuse; it checks again anyway.
  const canManage =
    software.can_manage &&
    software.supported_actions.includes("workload_restart") &&
    software.installed_release_id !== null &&
    snapshot?.managed !== false;
  // The whole workload is stopped: the device starts all of it, not one service.
  const allStopped = software.stopped_by_user && (snapshot?.stopped_services.length ?? 0) === 0;
  const canRefresh = software.supported_actions.includes("workload_snapshot");
  const pending = (kind: SoftwareActionKind, service?: string) =>
    waiting.some((a) => a.kind === kind && a.service === (service ?? null));

  function run(kind: SoftwareActionKind, service?: string) {
    const what = service ?? software!.workload_key;
    const question =
      kind === "workload_stop"
        ? `Stop ${what}? It stays stopped, also after a reboot, until you start it or deploy again.`
        : kind === "workload_restart"
          ? `Restart ${what}? It is unavailable for a moment.`
          : null;
    if (question && !window.confirm(question)) {
      return;
    }
    order.mutate({ kind, ...(service ? { service } : {}) });
  }

  const isWorkloadLog = (source: LogSource) =>
    software.kind === "systemd"
      ? source.kind === "systemd" && source.name === software.workload_key
      : source.kind === "docker" &&
        (software.kind === "docker_compose"
          ? source.project === software.workload_key
          : source.name === software.workload_key);

  const tabs: TabItem<Tab>[] = [
    { id: "overview", label: "Overview" },
    { id: "health", label: "Health" },
    ...(canLogs ? [{ id: "logs" as const, label: "Logs" }] : []),
    { id: "activity", label: "Activity" },
  ];
  const failed = actions[0]?.state === "failed" ? actions[0] : null;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Link to={deviceHref} className="text-sm text-link hover:underline">
          ← {device?.name ?? "Device"}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <h1 className="break-words text-2xl font-bold">{software.workload_key}</h1>
            {software.stopped_by_user ? (
              <StoppedBadge services={snapshot?.stopped_services} />
            ) : (
              <HealthBadge status={software.health} stale={device !== undefined && !online} />
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {canRefresh && (
              <Button
                type="button"
                variant="outline"
                disabled={order.isPending || pending("workload_snapshot")}
                onClick={() => run("workload_snapshot")}
              >
                {pending("workload_snapshot") ? "Refreshing…" : "Refresh now"}
              </Button>
            )}
            {canManage && (
              <>
                {software.stopped_by_user && (
                  <Button
                    type="button"
                    disabled={order.isPending || pending("workload_start")}
                    onClick={() => run("workload_start")}
                  >
                    Start
                  </Button>
                )}
                <Button
                  type="button"
                  variant="outline"
                  disabled={order.isPending || pending("workload_restart")}
                  onClick={() => run("workload_restart")}
                >
                  Restart
                </Button>
                {!allStopped && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={order.isPending || pending("workload_stop")}
                    onClick={() => run("workload_stop")}
                  >
                    Stop
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {software.installed_release
            ? `${software.installed_release.name} ${software.installed_release.version}`
            : (software.installed_version ?? "Not installed")}
          {software.desired_release && software.desired_release_id !== software.installed_release_id
            ? ` · wants ${software.desired_release.name} ${software.desired_release.version}`
            : ""}
          {software.snapshot_at ? ` · seen ${formatRelativeTime(software.snapshot_at)}` : ""}
        </p>
        {device && !online && (
          <p className="text-sm text-muted-foreground">
            The device is not connected: this is what it last reported. Actions wait until it is
            back, and expire if it takes too long.
          </p>
        )}
        {waiting.length > 0 && (
          <p role="status" className="text-sm text-muted-foreground">
            {waiting
              .map(
                (a) =>
                  `${ACTION_LABELS[a.kind]}${a.service ? ` ${a.service}` : ""}: ${STATE_LABELS[a.state].toLowerCase()}`,
              )
              .join(" · ")}
          </p>
        )}
        {(software.last_error || failed || order.isError) && (
          <p role="alert" className="text-sm text-destructive">
            {order.isError
              ? order.error instanceof ApiError
                ? order.error.message
                : "Could not order this action."
              : failed
                ? `${ACTION_LABELS[failed.kind]} failed: ${failed.last_error ?? "unknown error"}`
                : software.last_error}
          </p>
        )}
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Software sections" />

      {tab === "overview" && (
        <SoftwareOverview
          software={software}
          onService={canManage && !allStopped ? run : undefined}
        />
      )}
      {tab === "health" && (
        <SoftwareHealth
          token={token!}
          organizationId={organizationId}
          deviceId={deviceId}
          softwareId={softwareId}
        />
      )}
      {tab === "logs" && canLogs && (
        <DeviceLogs
          token={token!}
          organizationId={organizationId}
          deviceId={deviceId}
          deviceName={device?.name}
          only={isWorkloadLog}
        />
      )}
      {tab === "activity" && (
        <section className="space-y-3 rounded-lg border border-border bg-card p-4 shadow-glow sm:p-6">
          <h2 className="text-lg font-semibold">Activity</h2>
          {actions.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing was ordered yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1.5 pr-3 font-medium">When</th>
                    <th className="py-1.5 pr-3 font-medium">Action</th>
                    <th className="py-1.5 pr-3 font-medium">Result</th>
                    <th className="py-1.5 font-medium">By</th>
                  </tr>
                </thead>
                <tbody>
                  {actions.map((a) => (
                    <tr key={a.id} className="border-t border-border align-top">
                      <td className="whitespace-nowrap py-1.5 pr-3 tabular-nums text-muted-foreground">
                        {formatDateTime(a.created_at)}
                      </td>
                      <td className="py-1.5 pr-3">
                        {ACTION_LABELS[a.kind]}
                        {a.service ? ` ${a.service}` : ""}
                      </td>
                      <td className="py-1.5 pr-3">
                        {STATE_LABELS[a.state]}
                        {a.last_error && (
                          <p className="text-xs text-muted-foreground">{a.last_error}</p>
                        )}
                      </td>
                      <td className="py-1.5 text-muted-foreground">{a.requested_by ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
