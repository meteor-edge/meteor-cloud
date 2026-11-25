import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listDeployedApps, redeployApp, type DeployedApp } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { RunAppSheet } from "@/components/apps/RunAppSheet";
import { Button } from "@/components/ui/button";

type DeployedAppsProps = { token: string; organizationId: string; deviceId: string };

/** The device's single-container apps, with Edit and, when the registry has one, a newer image. */
export function DeployedApps({ token, organizationId, deviceId }: DeployedAppsProps) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<DeployedApp | null>(null);
  const queryKey = ["deployed-apps", organizationId, deviceId, token];
  const apps = useQuery({
    queryKey,
    queryFn: () => listDeployedApps(token, organizationId, deviceId),
  });
  const update = useMutation({
    mutationFn: ({ app, image }: { app: DeployedApp; image?: string }) =>
      redeployApp(token, organizationId, deviceId, app.workload_key, image ? { image } : {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["deployed-apps", organizationId] });
      await queryClient.invalidateQueries({ queryKey: ["device-software", organizationId] });
    },
  });
  const items = (apps.data ?? []).filter((app) => app.spec.image);
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">Apps</h3>
      <ul className="divide-y divide-border rounded-md border border-border">
        {items.map((app) => (
          <li
            key={app.workload_key}
            className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm"
          >
            <span className="min-w-0">
              <span className="font-medium">{app.workload_key}</span>{" "}
              <span className="break-all font-mono text-xs text-muted-foreground">
                {app.repository ? `${app.repository}:${app.tag}` : app.spec.image}
              </span>
            </span>
            <span className="flex flex-wrap gap-2">
              {app.tag_moved && (
                <Button
                  size="sm"
                  disabled={update.isPending}
                  onClick={() => update.mutate({ app })}
                >
                  Update {app.tag}
                </Button>
              )}
              {app.newer_tag && (
                <Button
                  size="sm"
                  disabled={update.isPending}
                  onClick={() =>
                    update.mutate({ app, image: `${app.repository}:${app.newer_tag}` })
                  }
                >
                  Update to {app.newer_tag}
                </Button>
              )}
              <Button
                size="sm"
                variant="secondary"
                aria-label={`Edit ${app.workload_key}`}
                onClick={() => setEditing(app)}
              >
                Edit
              </Button>
            </span>
          </li>
        ))}
      </ul>
      {update.isError && (
        <p role="alert" className="rounded-md bg-ember px-2 py-1 text-sm text-midnight">
          {update.error instanceof ApiError ? update.error.message : "Could not update the app."}
        </p>
      )}
      {editing && (
        <RunAppSheet
          token={token}
          organizationId={organizationId}
          deviceId={deviceId}
          app={editing}
          canPublish={false}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
