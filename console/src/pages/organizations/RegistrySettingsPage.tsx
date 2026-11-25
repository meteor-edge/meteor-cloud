import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import {
  createRegistryToken,
  getRegistry,
  listRegistryTokens,
  revokeRegistryToken,
  type RegistryToken,
  type RegistryTokenWithSecret,
} from "@/api/registry";
import { useAuth } from "@/auth/AuthContext";
import { OneTimeSecretDialog } from "@/components/fleet/OneTimeSecretDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageRegistryRobots, canPushImages } from "@/lib/permissions";
import { formatBytes, formatDateTime } from "@/lib/utils";

const selectClass = "h-10 w-full rounded-md border border-border bg-background px-3 text-sm";

function status(token: RegistryToken): string {
  if (token.revoked_at) return "Revoked";
  if (token.expires_at && new Date(token.expires_at) <= new Date()) return "Expired";
  return "Active";
}

export function RegistrySettingsPage() {
  const { organizationId = "" } = useParams();
  // Remounted per organization: a one-time secret must not stay open on another one's page.
  return <RegistrySettings key={organizationId} organizationId={organizationId} />;
}

function RegistrySettings({ organizationId }: { organizationId: string }) {
  const { token } = useAuth();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [kind, setKind] = useState<RegistryToken["kind"]>("personal");
  const [access, setAccess] = useState<RegistryToken["access"]>("push");
  const [prefix, setPrefix] = useState("");
  const [days, setDays] = useState("90");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<RegistryTokenWithSecret | null>(null);

  const enabled = Boolean(token && organizationId);
  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled,
  });
  const registryQuery = useQuery({
    queryKey: ["registry", organizationId, token],
    queryFn: () => getRegistry(token!, organizationId),
    enabled,
  });
  const registry = registryQuery.data;
  const tokensQuery = useQuery({
    queryKey: ["registry-tokens", organizationId, token],
    queryFn: () => listRegistryTokens(token!, organizationId),
    enabled: enabled && Boolean(registry?.enabled),
  });

  const role = orgQuery.data?.current_user_role;
  const canPush = canPushImages(role);
  const canRobots = canManageRegistryRobots(role);
  const tokenKind = canRobots ? kind : "personal";
  const repository = `${registry?.host}/${registry?.namespace}`;
  const tokens = tokensQuery.data ?? [];
  const loadFailed = orgQuery.isError || registryQuery.isError || tokensQuery.isError;

  const createMutation = useMutation({
    mutationFn: () =>
      createRegistryToken(token!, organizationId, {
        name,
        kind: tokenKind,
        access: canPush ? access : "pull",
        repository_prefix: tokenKind === "robot" && prefix.trim() ? prefix.trim() : null,
        expires_at: days ? new Date(Date.now() + Number(days) * 86_400_000).toISOString() : null,
      }),
    onSuccess: async (result) => {
      setCreated(result);
      setName("");
      setPrefix("");
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["registry-tokens", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create the token.");
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  async function onRevoke(entry: RegistryToken) {
    if (!window.confirm(`Revoke ${entry.name}? Anything signed in with it stops working.`)) return;
    setError(null);
    try {
      await revokeRegistryToken(token!, organizationId, entry.id);
      await queryClient.invalidateQueries({ queryKey: ["registry-tokens", organizationId] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke the token.");
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Registry</h1>
          <p className="mt-2 text-muted-foreground">
            Tokens for pushing and pulling container images of{" "}
            {orgQuery.data?.name ?? "this organization"}.
          </p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/settings`}>Back to settings</Link>
        </Button>
      </div>

      {(error || loadFailed) && (
        <p
          role="alert"
          className="text-sm font-medium text-midnight bg-ember rounded-md px-2 py-1"
        >
          {error ?? "Could not load the registry settings."}
        </p>
      )}

      {registry && !registry.enabled && (
        <p className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground">
          The container registry is not enabled on this platform.
        </p>
      )}

      {registry?.enabled && (
        <>
          <div className="rounded-lg border border-border bg-card p-5 shadow-glow">
            <p className="text-sm font-medium">Push an image</p>
            <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background px-3 py-2 text-xs">
              {[
                `docker login ${registry.host}`,
                `docker tag my-app ${repository}/my-app:1.0`,
                `docker push ${repository}/my-app:1.0`,
              ].join("\n")}
            </pre>
            <p className="mt-2 text-xs text-muted-foreground">
              Use a token below as the password. The user name can be anything.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Storage used: {formatBytes(registry.used_bytes ?? 0)}
              {registry.quota_bytes ? ` of ${formatBytes(registry.quota_bytes)}` : ""}
            </p>
          </div>

          <form
            className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow md:grid-cols-3"
            onSubmit={onSubmit}
          >
            <div>
              <Label htmlFor="token-name">Name</Label>
              <Input
                id="token-name"
                value={name}
                maxLength={120}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            {canRobots && (
              <div>
                <Label htmlFor="token-kind">Used by</Label>
                <select
                  id="token-kind"
                  className={selectClass}
                  value={kind}
                  onChange={(event) => setKind(event.target.value as RegistryToken["kind"])}
                >
                  <option value="personal">Me (personal)</option>
                  <option value="robot">CI (robot)</option>
                </select>
              </div>
            )}
            {canPush && (
              <div>
                <Label htmlFor="token-access">Access</Label>
                <select
                  id="token-access"
                  className={selectClass}
                  value={access}
                  onChange={(event) => setAccess(event.target.value as RegistryToken["access"])}
                >
                  <option value="push">Push and pull</option>
                  <option value="pull">Pull only</option>
                </select>
              </div>
            )}
            {tokenKind === "robot" && (
              <div>
                <Label htmlFor="token-prefix">Only repositories under (optional)</Label>
                <Input
                  id="token-prefix"
                  value={prefix}
                  placeholder="ci"
                  onChange={(event) => setPrefix(event.target.value)}
                />
              </div>
            )}
            <div>
              <Label htmlFor="token-expiry">Expires</Label>
              <select
                id="token-expiry"
                className={selectClass}
                value={days}
                onChange={(event) => setDays(event.target.value)}
              >
                <option value="30">In 30 days</option>
                <option value="90">In 90 days</option>
                <option value="365">In 1 year</option>
                <option value="">Never</option>
              </select>
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={createMutation.isPending || !orgQuery.isSuccess}>
                Create token
              </Button>
            </div>
          </form>

          <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  {["Name", "Type", "Access", "Prefix", "Expires", "Last used", "Status", ""].map(
                    (heading) => (
                      <th key={heading} className="px-4 py-3 font-semibold">
                        {heading}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {tokens.map((entry) => (
                  <tr key={entry.id} className="border-b border-border">
                    <td className="px-4 py-3 font-medium">{entry.name}</td>
                    <td className="px-4 py-3">{entry.kind === "robot" ? "Robot" : "Personal"}</td>
                    <td className="px-4 py-3">
                      {entry.access === "push" ? "Push and pull" : "Pull only"}
                      {entry.repository_prefix && ` (${entry.repository_prefix}/*)`}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{entry.token_prefix}…</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {entry.expires_at ? formatDateTime(entry.expires_at) : "Never"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {entry.last_used_at ? formatDateTime(entry.last_used_at) : "Never"}
                    </td>
                    <td className="px-4 py-3">{status(entry)}</td>
                    <td className="px-4 py-3">
                      {!entry.revoked_at && (
                        <Button variant="ghost" size="sm" onClick={() => onRevoke(entry)}>
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
                {tokensQuery.isSuccess && tokens.length === 0 && (
                  <tr>
                    <td className="px-4 py-6 text-center text-muted-foreground" colSpan={8}>
                      No registry tokens yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {created && registry?.host && (
        <OneTimeSecretDialog
          title="Registry token created"
          description="Copy this token now and use it as the docker login password. It will not be shown again."
          secret={created.token}
          command={`docker login ${registry.host} -u ${created.kind === "robot" ? "robot" : "me"}`}
          commandLabel="Sign in"
          commandHint={
            <>
              Docker asks for the password: paste the token. In CI, pipe it to{" "}
              <code>--password-stdin</code>.
            </>
          }
          onClose={() => setCreated(null)}
        />
      )}
    </section>
  );
}
