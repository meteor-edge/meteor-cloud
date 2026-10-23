import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createEndpoint, deleteEndpoint, listEndpoints, updateEndpoint } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { AppAddressPicker } from "@/components/fleet/AppAddressPicker";
import { AvailabilityBadge } from "@/components/fleet/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type DeviceEndpointsPanelProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  canManage: boolean;
};

export function DeviceEndpointsPanel({
  token,
  organizationId,
  deviceId,
  canManage,
}: DeviceEndpointsPanelProps) {
  const queryClient = useQueryClient();
  const [hostname, setHostname] = useState<string | null>(null);
  const [appName, setAppName] = useState("");
  const [formKey, setFormKey] = useState(0);
  const [port, setPort] = useState("80");
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["endpoints", organizationId, deviceId, token],
    queryFn: () => listEndpoints(token, organizationId, deviceId),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createEndpoint(token, organizationId, {
        device_id: deviceId,
        hostname: hostname ?? "",
        port: Number(port),
      }),
    onSuccess: () => {
      setAppName("");
      setFormKey((key) => key + 1);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["endpoints", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create the endpoint.");
    },
  });

  const items = query.data ?? [];

  return (
    <div className="space-y-4">
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No public URLs on this device.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm"
            >
              <div>
                <a
                  className="font-medium text-link underline"
                  href={item.public_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {item.hostname}
                </a>
                <p className="text-muted-foreground">
                  port {item.port} · <AvailabilityBadge status={item.availability} />
                </p>
              </div>
              {canManage && (
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      void updateEndpoint(token, organizationId, item.id, {
                        enabled: !item.enabled,
                      }).then(() =>
                        queryClient.invalidateQueries({ queryKey: ["endpoints", organizationId] }),
                      )
                    }
                  >
                    {item.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      void deleteEndpoint(token, organizationId, item.id).then(() =>
                        queryClient.invalidateQueries({ queryKey: ["endpoints", organizationId] }),
                      )
                    }
                  >
                    Delete
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <form
          className="space-y-3 rounded-md border border-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate();
          }}
        >
          <p className="text-sm font-medium">Publish an app on this device</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1">
              <label className="text-xs font-medium" htmlFor="endpoint-app">
                App name
              </label>
              <Input
                id="endpoint-app"
                value={appName}
                onChange={(event) => setAppName(event.target.value)}
                placeholder="shop"
              />
            </div>
            <div className="w-24">
              <label className="text-xs font-medium" htmlFor="endpoint-port">
                Port
              </label>
              <Input
                id="endpoint-port"
                type="number"
                min={1}
                max={65535}
                value={port}
                onChange={(event) => setPort(event.target.value)}
              />
            </div>
          </div>
          <AppAddressPicker
            key={formKey}
            token={token}
            organizationId={organizationId}
            deviceId={deviceId}
            appName={appName}
            onChange={setHostname}
          />
          <Button type="submit" disabled={createMutation.isPending || !hostname}>
            Add URL
          </Button>
        </form>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
