import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import {
  listDeviceActions,
  updateDeviceAgent,
  type DeviceAction,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { cn, formatDateTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
  agentVersion: string | null;
};

function isInFlight(action: DeviceAction): boolean {
  return action.state === "pending" || action.state === "sent";
}

function latestUpdate(actions: DeviceAction[]): DeviceAction | null {
  return actions.find((action) => action.kind === "update_agent") ?? null;
}

function statusNote(action: DeviceAction): { text: string; tone: string } {
  if (action.state === "pending") {
    return { text: "Waiting for the device (its next heartbeat).", tone: "text-muted-foreground" };
  }
  if (action.state === "sent") {
    return {
      text: "The device is installing the latest meteorcli.",
      tone: "text-muted-foreground",
    };
  }
  if (action.state === "failed") {
    return {
      text: action.last_error ?? "The device could not update meteorcli.",
      tone: "text-destructive",
    };
  }
  const when = action.reported_at ? ` ${formatDateTime(action.reported_at)}` : "";
  return { text: `Updated to the latest release${when}.`, tone: "text-muted-foreground" };
}

/** Order meteorcli on this device to install the latest published release. */
export function AgentUpdatePanel({ token, organizationId, deviceId, agentVersion }: Props) {
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
    onSuccess: (data) => {
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
        Installed version:{" "}
        <span className="font-medium">{agentVersion ?? "Not reported yet"}</span>
      </p>

      <Button
        variant="outline"
        disabled={busy}
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
      {note && <p className={`text-sm ${note.tone}`}>{note.text}</p>}
    </div>
  );
}
