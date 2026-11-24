import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";

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
import { agentModuleDescription } from "@/lib/agentModules";
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

/** A module's description, shown above it on hover or keyboard focus (parent needs `group relative`). */
function ModuleTooltip({ id, name }: { id: string; name: string }) {
  const text = agentModuleDescription(name);
  if (!text) return null;
  return (
    <span
      id={id}
      role="tooltip"
      className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-2 w-72 max-w-[80vw] -translate-x-1/2 rounded-md border border-border bg-card px-3 py-2 text-xs font-normal leading-relaxed text-foreground opacity-0 shadow-lg transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
    >
      {text}
    </span>
  );
}

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

  const modules = caps.modules === null ? null : sortedModules(caps.modules);
  // Rows: modules that need attention (broken, or a restart waiting or failed). Tiles: the rest.
  const needsRow = ([name, health]: [string, ModuleHealth]) =>
    isBroken(health) || Boolean(pendingRestart(name)) || lastRestart(name)?.state === "failed";
  const problemRows = modules?.filter(needsRow) ?? [];
  const tiles = modules?.filter((entry) => !needsRow(entry)) ?? [];
  const running = modules?.filter(([, h]) => h.state === "running").length ?? 0;
  const broken = modules?.filter(([, h]) => isBroken(h)).length ?? 0;

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">Agent modules</h2>
            {modules && (
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
                  broken > 0 ? "bg-ember text-midnight" : "bg-aurora/15 text-aurora",
                )}
              >
                {broken > 0
                  ? `${broken} need${broken === 1 ? "s" : ""} attention`
                  : running === modules.length
                    ? `All ${modules.length} running`
                    : `${running} of ${modules.length} running`}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            A crashing module restarts on its own; after five crashes in ten minutes it stays
            stopped until you restart it. The other modules keep working.
          </p>
          {caps.reportedAt && (
            <p className="mt-1 text-xs text-muted-foreground">
              Reported {formatDateTime(caps.reportedAt)}
            </p>
          )}
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

      {modules === null ? (
        <p className="text-sm text-muted-foreground">
          This device&apos;s agent does not report module health. Update meteorcli to see it.
        </p>
      ) : (
        <>
          {problemRows.length > 0 && (
            <ul className="space-y-2">
              {problemRows.map(([name, health]) => {
                const pending = pendingRestart(name);
                const last = lastRestart(name);
                return (
                  <li
                    key={name}
                    className={cn(
                      "flex flex-wrap items-center justify-between gap-3 rounded-md border p-3",
                      isBroken(health) ? "border-ember/60 bg-ember/10" : "border-border",
                    )}
                    data-testid={`module-${name}`}
                  >
                    <div className="min-w-0 space-y-1">
                      <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        <span className="font-mono">{name}</span>
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
                      {agentModuleDescription(name) && (
                        <p className="text-xs text-muted-foreground">
                          {agentModuleDescription(name)}
                        </p>
                      )}
                      {isBroken(health) && health.last_error && (
                        <p className="break-all font-mono text-xs text-destructive">
                          {health.last_error}
                        </p>
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
          {tiles.length > 0 && (
            <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
              {tiles.map(([name, health]) => (
                <li
                  key={name}
                  className="group relative flex min-w-0 items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2"
                  data-testid={`module-${name}`}
                >
                  <ModuleTooltip id={`module-tip-${name}`} name={name} />
                  <span
                    aria-hidden
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      health.state === "running"
                        ? "bg-aurora"
                        : health.state === "stopped"
                          ? "bg-muted-foreground"
                          : "bg-horizon",
                    )}
                  />
                  <div
                    className="min-w-0 flex-1 cursor-help rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    tabIndex={agentModuleDescription(name) ? 0 : undefined}
                    aria-describedby={
                      agentModuleDescription(name) ? `module-tip-${name}` : undefined
                    }
                  >
                    <p className="truncate font-mono text-sm">{name}</p>
                    <p className="text-xs text-muted-foreground">
                      {STATE_LABELS[health.state] ?? health.state}
                      {health.restarts
                        ? ` · ${health.restarts} restart${health.restarts === 1 ? "" : "s"}`
                        : ""}
                    </p>
                  </div>
                  {canRestart && (
                    <Button
                      variant="ghost"
                      className="h-7 w-7 shrink-0 p-0 text-muted-foreground opacity-60 hover:opacity-100 group-hover:opacity-100"
                      aria-label="Restart"
                      title={`Restart ${name}`}
                      disabled={busy}
                      onClick={() => restartMutation.mutate(name)}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
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
