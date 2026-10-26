import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  cancelDeployment,
  getDeployment,
  retryFailedDeployment,
  rollbackDeployment,
} from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/AuthContext";
import { AvailabilityBadge, HealthBadge } from "@/components/fleet/StatusBadge";
import { Button } from "@/components/ui/button";
import { canManageDeployments, canRollbackDeployments } from "@/lib/permissions";

const STATUS_LABELS: Record<string, string> = {
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
  pending: "Pending",
  queued: "Queued",
  updating: "Updating",
  succeeded: "Succeeded",
  skipped: "Skipped",
};

export function DeploymentDetailPage() {
  const { organizationId = "", deploymentId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"cancel" | "retry" | "rollback" | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const detailQuery = useQuery({
    queryKey: ["deployment", organizationId, deploymentId, token],
    queryFn: () => getDeployment(token!, organizationId, deploymentId),
    enabled: Boolean(token && organizationId && deploymentId),
    // Health and URLs keep changing after install finishes, so keep polling (slower).
    refetchInterval: (query) => (query.state.data?.status === "in_progress" ? 5000 : 15000),
    refetchOnWindowFocus: true,
  });

  const canManage = canManageDeployments(orgQuery.data?.current_user_role);
  const canRollback = canRollbackDeployments(orgQuery.data?.current_user_role);
  const deployment = detailQuery.data;
  const releaseLabel = deployment?.release
    ? `${deployment.release.name} ${deployment.release.version}`
    : deployment?.release_id;
  const failedCount = deployment?.counts.failed ?? 0;
  const rows = deployment?.devices ?? [];
  const healthyCount = rows.filter((row) => row.health === "healthy").length;
  const urlCount = rows.filter((row) => row.endpoint_availability !== null).length;
  const urlReadyCount = rows.filter((row) => row.endpoint_availability === "ready").length;

  async function onCancel() {
    if (!token || !deployment || !window.confirm("Cancel this deployment?")) {
      return;
    }
    setBusy("cancel");
    setError(null);
    try {
      await cancelDeployment(token, organizationId, deploymentId);
      await queryClient.invalidateQueries({
        queryKey: ["deployment", organizationId, deploymentId],
      });
      await queryClient.invalidateQueries({ queryKey: ["deployments", organizationId] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not cancel the deployment.");
    } finally {
      setBusy(null);
    }
  }

  async function onRetry() {
    if (!token || !deployment || !window.confirm("Retry failed devices?")) {
      return;
    }
    setBusy("retry");
    setError(null);
    try {
      await retryFailedDeployment(token, organizationId, deploymentId);
      await queryClient.invalidateQueries({
        queryKey: ["deployment", organizationId, deploymentId],
      });
      await queryClient.invalidateQueries({ queryKey: ["deployments", organizationId] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not retry failed devices.");
    } finally {
      setBusy(null);
    }
  }

  async function onRollback() {
    if (
      !token ||
      !deployment ||
      !window.confirm(
        "Roll back to the previous release? This starts a new Deploy for the prior desired software.",
      )
    ) {
      return;
    }
    setBusy("rollback");
    setError(null);
    try {
      const created = await rollbackDeployment(token, organizationId, deploymentId);
      await queryClient.invalidateQueries({ queryKey: ["deployments", organizationId] });
      navigate(`/organizations/${organizationId}/deployments/${created.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not roll back the deployment.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <Link
          to={`/organizations/${organizationId}/deployments`}
          className="text-sm text-link hover:underline"
        >
          ← Deployments
        </Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">
              {deployment?.name || releaseLabel || "Deployment"}
            </h1>
            <p className="mt-2 text-muted-foreground">
              {releaseLabel}
              {deployment ? ` · ${STATUS_LABELS[deployment.status] ?? deployment.status}` : ""}
              {deployment?.rollback_of_deployment_id ? " · rollback" : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {canManage && failedCount > 0 && (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={() => void onRetry()}
              >
                {busy === "retry" ? "Retrying…" : "Retry failed"}
              </Button>
            )}
            {canRollback && deployment && deployment.status !== "in_progress" && (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={() => void onRollback()}
              >
                {busy === "rollback" ? "Rolling back…" : "Rollback"}
              </Button>
            )}
            {canManage && deployment?.status === "in_progress" && (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={() => void onCancel()}
              >
                {busy === "cancel" ? "Cancelling…" : "Cancel"}
              </Button>
            )}
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {detailQuery.isError && (
        <p className="text-sm text-destructive">Could not load this deployment.</p>
      )}

      {deployment && (
        <p className="text-sm text-muted-foreground">
          {deployment.counts.succeeded}/{deployment.counts.total} installed · {healthyCount} healthy
          {urlCount > 0 ? ` · ${urlReadyCount} URL ready` : ""}
          {deployment.counts.pending > 0 ? ` · ${deployment.counts.pending} pending` : ""}
          {deployment.counts.queued > 0 ? ` · ${deployment.counts.queued} queued` : ""}
          {deployment.counts.updating > 0 ? ` · ${deployment.counts.updating} updating` : ""}
          {deployment.counts.failed > 0 ? ` · ${deployment.counts.failed} failed` : ""}
          {deployment.counts.cancelled > 0 ? ` · ${deployment.counts.cancelled} cancelled` : ""}
          {deployment.counts.skipped > 0 ? ` · ${deployment.counts.skipped} skipped` : ""}
        </p>
      )}

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">Device</th>
              <th className="px-4 py-3 font-medium">Install</th>
              <th className="px-4 py-3 font-medium">Health</th>
              <th className="px-4 py-3 font-medium">URL</th>
              <th className="px-4 py-3 font-medium">Error</th>
              <th className="px-4 py-3 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <Link
                    to={`/organizations/${organizationId}/devices/${row.device_id}`}
                    className="font-medium text-link hover:underline"
                  >
                    {row.device_name || row.device_id}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  {STATUS_LABELS[row.status] ?? row.status}
                  {row.status === "updating" && row.progress_percent != null
                    ? ` (${row.progress_percent}%)`
                    : ""}
                </td>
                <td className="px-4 py-3">
                  {row.health ? <HealthBadge status={row.health} /> : "—"}
                </td>
                <td className="px-4 py-3">
                  {row.endpoint_availability ? (
                    <AvailabilityBadge status={row.endpoint_availability} />
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-3 text-muted-foreground">{row.last_error || "—"}</td>
                <td className="px-4 py-3 text-muted-foreground">
                  {new Date(row.updated_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {deployment && (deployment.devices?.length ?? 0) === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                  No devices on this deployment.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
