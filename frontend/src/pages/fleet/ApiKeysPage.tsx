import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import {
  createEnrollmentKey,
  listEnrollmentKeys,
  revokeEnrollmentKey,
  type EnrollmentApiKeyWithSecret,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { FleetNav } from "@/components/fleet/FleetNav";
import { OneTimeSecretDialog } from "@/components/fleet/OneTimeSecretDialog";
import { PendingEnrollmentRequests } from "@/components/fleet/PendingEnrollmentRequests";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageFleet } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

function buildConfigCommand(domain: string, apiKey: string): string {
  return [
    "meteorcli config \\",
    `  --domain ${domain} \\`,
    `  --api-key ${apiKey}`,
  ].join("\n");
}

export function ApiKeysPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();

  const [keyName, setKeyName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<EnrollmentApiKeyWithSecret | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const keysQuery = useQuery({
    queryKey: ["enrollment-keys", organizationId, token],
    queryFn: () => listEnrollmentKeys(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  const createMutation = useMutation({
    mutationFn: () =>
      createEnrollmentKey(token!, organizationId, {
        name: keyName,
      }),
    onSuccess: async (created) => {
      setCreatedKey(created);
      setKeyName("");
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["enrollment-keys", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create the API key.");
    },
  });

  function onCreateKey(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  async function onRevoke(keyId: string) {
    if (!window.confirm("Revoke this API key?")) {
      return;
    }
    setError(null);
    try {
      await revokeEnrollmentKey(token!, organizationId, keyId);
      await queryClient.invalidateQueries({ queryKey: ["enrollment-keys", organizationId] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke the API key.");
    }
  }

  const domain = window.location.hostname.replace(/^www\./, "");

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Fleet</h1>
        <p className="mt-2 text-muted-foreground">{orgQuery.data?.name}</p>
      </div>
      <FleetNav organizationId={organizationId} />

      {error && <p className="text-sm text-red-700">{error}</p>}

      {token && (
        <PendingEnrollmentRequests
          organizationId={organizationId}
          token={token}
          canManage={canManage}
          onError={setError}
        />
      )}

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-white/80 p-5 shadow-sm md:grid-cols-2"
          onSubmit={onCreateKey}
        >
          <div className="md:col-span-2">
            <p className="text-sm font-medium">API keys</p>
            <p className="text-xs text-muted-foreground">
              Devices use a key with <code>meteorcli config</code> and{" "}
              <code>meteorcli request-token</code> to request a device token
              and connect to this organization.
            </p>
          </div>
          <div>
            <Label htmlFor="key-name">Name</Label>
            <Input
              id="key-name"
              value={keyName}
              onChange={(event) => setKeyName(event.target.value)}
              required
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Create API key
            </Button>
          </div>
        </form>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-white/80 shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Prefix</th>
              <th className="px-4 py-3 font-semibold">Created</th>
              <th className="px-4 py-3 font-semibold">Status</th>
              {canManage && <th className="px-4 py-3 font-semibold">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {(keysQuery.data ?? []).map((entry) => (
              <tr key={entry.id} className="border-b border-border/70">
                <td className="px-4 py-3 font-medium">{entry.name}</td>
                <td className="px-4 py-3 font-mono text-xs">{entry.key_prefix}…</td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDateTime(entry.created_at)}
                </td>
                <td className="px-4 py-3">{entry.revoked_at ? "Revoked" : "Active"}</td>
                {canManage && (
                  <td className="px-4 py-3">
                    {!entry.revoked_at && (
                      <Button variant="ghost" size="sm" onClick={() => onRevoke(entry.id)}>
                        Revoke
                      </Button>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {(keysQuery.data ?? []).length === 0 && (
              <tr>
                <td
                  className="px-4 py-6 text-center text-muted-foreground"
                  colSpan={canManage ? 5 : 4}
                >
                  No API keys yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {createdKey && (
        <OneTimeSecretDialog
          title="API key created"
          description="Copy this key now and configure meteorcli with it. For security it will not be shown again."
          secret={createdKey.api_key}
          command={buildConfigCommand(domain, createdKey.api_key)}
          onClose={() => setCreatedKey(null)}
        />
      )}
    </section>
  );
}
