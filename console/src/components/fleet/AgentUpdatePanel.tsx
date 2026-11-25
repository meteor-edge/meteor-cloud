import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";

import { listDeviceActions, updateDeviceAgent, type DeviceAction } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { explainUpdateError } from "@/lib/agentUpdateErrors";
import { cn, formatDateTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
  agentVersion: string | null;
  /** False when the device's agent says it cannot update itself. */
  supported?: boolean;
};

function isInFlight(action: DeviceAction): boolean {
  return action.state === "pending" || action.state === "sent";
}

function latestUpdate(actions: DeviceAction[]): DeviceAction | null {
  return actions.find((action) => action.kind === "update_agent") ?? null;
}

type UpdateNote = {
  tone: "info" | "success" | "error";
  title: string;
  hint?: string;
  /** The device's own error text, shown small under the explanation. */
  detail?: string;
  /** When the failed attempt was reported. */
  when?: string;
};

function statusNote(action: DeviceAction): UpdateNote {
  if (action.state === "pending") {
    return { tone: "info", title: "Waiting for the device (its next heartbeat)." };
  }
  if (action.state === "sent") {
    return { tone: "info", title: "The device is installing the latest meteorcli." };
  }
  if (action.state === "failed") {
    return {
      tone: "error",
      ...explainUpdateError(action.last_error),
      when: formatDateTime(action.reported_at ?? action.created_at),
    };
  }
  const when = action.reported_at ? ` ${formatDateTime(action.reported_at)}` : "";
  return { tone: "success", title: `Updated to the latest release${when}.` };
}

/** Order meteorcli on this device to install the latest published release. */
export function AgentUpdatePanel({
  token,
  organizationId,
  deviceId,
  agentVersion,
  supported = true,
}: Props) {
  const queryClient = useQueryClient();
  const queryKey = ["device-actions", organizationId, deviceId];
  const deviceKey = ["device", organizationId, deviceId];
  const [error, setError] = useState<string | null>(null);
  const [watchUntilSettled, setWatchUntilSettled] = useState(false);

  const actionsQuery = useQuery({
    queryKey,
    queryFn: () => listDeviceActions(token, organizationId, deviceId),
    refetchInterval: (query) => {
      const update = latestUpdate(query.state.data?.actions ?? []);
      if (watchUntilSettled || (update && isInFlight(update))) {
        return 5000;
      }
      return 30000;
    },
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    const update = latestUpdate(actionsQuery.data?.actions ?? []);
    if (watchUntilSettled && update && !isInFlight(update)) {
      setWatchUntilSettled(false);
      void queryClient.invalidateQueries({ queryKey: deviceKey });
    }
  }, [actionsQuery.data, watchUntilSettled, queryClient, organizationId, deviceId, deviceKey]);

  const mutation = useMutation({
    mutationFn: () => updateDeviceAgent(token, organizationId, deviceId),
    onSuccess: async (data) => {
      // Drop in-flight list fetches so a stale response cannot overwrite this write.
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, data);
      setWatchUntilSettled(true);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: deviceKey });
    },
    onError: (reason: unknown) =>
      setError(reason instanceof ApiError ? reason.message : "The update could not be ordered."),
  });

  const update = latestUpdate(actionsQuery.data?.actions ?? []);
  const inFlight = update ? isInFlight(update) : false;
  const busy = mutation.isPending || inFlight;
  const refreshing = actionsQuery.isFetching && !mutation.isPending;
  const note = update ? statusNote(update) : null;

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Agent update</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Install the latest published meteorcli on this device. The device checks the release
            checksum, keeps the previous install, and puts it back if the new service does not stay
            up.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          className="h-10 w-10 shrink-0 rounded-full p-0"
          aria-label="Refresh agent update"
          title="Refresh"
          disabled={refreshing}
          onClick={() => {
            void actionsQuery.refetch();
            void queryClient.invalidateQueries({ queryKey: deviceKey });
          }}
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
        </Button>
      </div>

      <p className="text-sm">
        Installed version: <span className="font-medium">{agentVersion ?? "Not reported yet"}</span>
      </p>

      {!supported && (
        <p className="text-sm text-muted-foreground">
          The agent on this device cannot update itself. Run the installer on the device once.
        </p>
      )}
      <Button
        variant="outline"
        disabled={busy || !supported}
        onClick={() => {
          if (
            window.confirm(
              "Update this device to the latest meteorcli? The agent service restarts briefly.",
            )
          ) {
            mutation.mutate();
          }
        }}
      >
        {inFlight ? "Updating…" : "Update to latest"}
      </Button>

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}
      {note && note.tone === "error" && (
        <div
          role="alert"
          className="flex gap-3 rounded-md border border-ember/60 bg-ember/10 p-3 text-sm"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ember" aria-hidden />
          <div className="min-w-0 space-y-1">
            <p className="font-medium">
              {note.title}
              {note.when && (
                <span className="ml-2 font-normal text-muted-foreground">{note.when}</span>
              )}
            </p>
            {note.hint && <p className="text-muted-foreground">{note.hint}</p>}
            {note.detail && (
              <p className="break-all font-mono text-xs text-muted-foreground">{note.detail}</p>
            )}
          </div>
        </div>
      )}
      {note && note.tone !== "error" && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          {note.tone === "success" ? (
            <CheckCircle2 className="h-4 w-4 text-aurora" aria-hidden />
          ) : (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          )}
          {note.title}
        </p>
      )}
    </div>
  );
}
