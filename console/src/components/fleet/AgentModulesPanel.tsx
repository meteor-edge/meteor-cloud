import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listDeviceActions,
  restartDeviceModule,
  runDeviceDiagnostics,
  type Device,
  type DeviceAction,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { agentCapabilities, supportsAction, type ModuleHealth } from "@/lib/agentCapabilities";
import { cn, formatDateTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
  device: Pick<Device, "metadata" | "status">;
  /** May restart modules and run diagnostics (device.update_agent). */
  canRepair: boolean;
};

const STATE_STYLES: Record<string, string> = {
  running: "bg-aurora text-midnight",
  starting: "bg-horizon text-star-dust",
  waiting: "bg-horizon text-star-dust",
  stopped: "bg-muted text-muted-foreground",
  failed: "bg-ember text-midnight",
  parked: "bg-ember text-midnight",
};

const STATE_LABELS: Record<string, string> = {
  running: "Running",
  starting: "Starting",
  waiting: "Waiting",
  stopped: "Stopped",
  failed: "Failed (restarting)",
  parked: "Failed (stopped)",
};

function isBroken(health: ModuleHealth): boolean {
  return health.state === "failed" || health.state === "parked";
}

function isInFlight(action: DeviceAction): boolean {
  return action.state === "pending" || action.state === "sent";
}

/** Broken modules first, then by name. */
function sortedModules(modules: Record<string, ModuleHealth>): [string, ModuleHealth][] {
  return Object.entries(modules).sort(([a, ha], [b, hb]) => {
    const rank = (h: ModuleHealth) => (h.state === "parked" ? 0 : h.state === "failed" ? 1 : 2);
    return rank(ha) - rank(hb) || a.localeCompare(b);
  });
}

/**
 * Health of each meteorcli module (from the last heartbeat), with remote repair:
 * restart one module (a module stopped after crashing again and again runs again)
 * and run the agent's doctor checks.
 */
export function AgentModulesPanel({ token, organizationId, deviceId, device, canRepair }: Props) {
  const queryClient = useQueryClient();
  const queryKey = ["device-actions", organizationId, deviceId];
  const caps = agentCapabilities(device);
  const canRestart = canRepair && supportsAction(caps, "restart_module");
  const canDiagnose = canRepair && supportsAction(caps, "run_diagnostics");
  const [error, setError] = useState<string | null>(null);
  const [watch, setWatch] = useState(false);

  const actionsQuery = useQuery({
    queryKey,
    queryFn: () => listDeviceActions(token, organizationId, deviceId),
    enabled: canRepair,
    refetchInterval: (query) =>
      watch || (query.state.data?.actions ?? []).some(isInFlight) ? 5000 : 30000,
  });
  const actions = actionsQuery.data?.actions ?? [];
  const repairs = actions.filter(
    (action) => action.kind === "restart_module" || action.kind === "run_diagnostics",
  );

  useEffect(() => {
    if (watch && repairs.length > 0 && !repairs.some(isInFlight)) {
      setWatch(false);
      void queryClient.invalidateQueries({ queryKey: ["device", organizationId, deviceId] });
    }
  }, [repairs, watch, queryClient, organizationId, deviceId]);

  const onDone = async (data: { actions: DeviceAction[] }) => {
    await queryClient.cancelQueries({ queryKey });
    queryClient.setQueryData(queryKey, data);
    setWatch(true);
    setError(null);
  };
  const onError = (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : "The request could not be saved.");

  const restartMutation = useMutation({
    mutationFn: (module: string) => restartDeviceModule(token, organizationId, deviceId, module),
    onSuccess: onDone,
    onError,
  });
  const diagnoseMutation = useMutation({
    mutationFn: () => runDeviceDiagnostics(token, organizationId, deviceId),
    onSuccess: onDone,
    onError,
  });

  const pendingRestart = (module: string) =>
    repairs.find((a) => a.kind === "restart_module" && a.module === module && isInFlight(a));
  const lastRestart = (module: string) =>
    repairs.find((a) => a.kind === "restart_module" && a.module === module);
  const diagnostics = repairs.find((a) => a.kind === "run_diagnostics") ?? null;
  const busy = restartMutation.isPending || diagnoseMutation.isPending;

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Agent modules</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Each part of meteorcli runs on its own; a crashing module is restarted automatically and
            stopped after crashing five times in ten minutes, while the others keep working.
            {caps.reportedAt ? ` Reported ${formatDateTime(caps.reportedAt)}.` : ""}
          </p>
        </div>
        {canDiagnose && (
          <Button
            variant="outline"
            disabled={busy || (diagnostics !== null && isInFlight(diagnostics))}
            onClick={() => diagnoseMutation.mutate()}
          >
            {diagnostics && isInFlight(diagnostics) ? "Diagnosing…" : "Run diagnostics"}
          </Button>
        )}
      </div>

      {caps.modules === null ? (
        <p className="text-sm text-muted-foreground">
          This device&apos;s agent does not report module health. Update meteorcli to see it.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {sortedModules(caps.modules).map(([name, health]) => {
            const pending = pendingRestart(name);
            const last = lastRestart(name);
            return (
              <li
                key={name}
                className="flex flex-wrap items-center justify-between gap-3 p-3"
                data-testid={`module-${name}`}
              >
                <div className="min-w-0 space-y-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {name}
                    <span
                      className={cn(
                        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                        STATE_STYLES[health.state] ?? STATE_STYLES.stopped,
                      )}
                    >
                      {STATE_LABELS[health.state] ?? health.state}
                    </span>
                    {health.restarts ? (
                      <span className="text-xs text-muted-foreground">
                        {health.restarts} restart{health.restarts === 1 ? "" : "s"}
                      </span>
                    ) : null}
                  </p>
                  {isBroken(health) && health.last_error && (
                    <p className="break-all text-xs text-destructive">{health.last_error}</p>
                  )}
                  {pending && (
                    <p className="text-xs text-muted-foreground">
                      Restart waiting for the device (its next heartbeat).
                    </p>
                  )}
                  {!pending && last?.state === "failed" && (
                    <p className="text-xs text-destructive">
                      Restart failed: {last.last_error ?? "the device could not restart it."}
                    </p>
                  )}
                </div>
                {canRestart && (
                  <Button
                    variant={isBroken(health) ? "default" : "outline"}
                    disabled={busy || Boolean(pending)}
                    onClick={() => restartMutation.mutate(name)}
                  >
                    Restart
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      {diagnostics && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">
            Diagnostics{" "}
            <span className="font-normal text-muted-foreground">
              {formatDateTime(diagnostics.reported_at ?? diagnostics.created_at)}
            </span>
          </h3>
          {isInFlight(diagnostics) && (
            <p className="text-sm text-muted-foreground">
              Waiting for the device (its next heartbeat).
            </p>
          )}
          {diagnostics.state === "failed" && (
            <p className="text-sm text-destructive">
              {diagnostics.last_error ?? "The device could not run the checks."}
            </p>
          )}
          {diagnostics.state === "done" && (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs">
              {diagnostics.output ?? "The device sent no report."}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
