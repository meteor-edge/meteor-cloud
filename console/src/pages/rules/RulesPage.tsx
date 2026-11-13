import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  createIntegration,
  deleteIntegration,
  listIntegrations,
  listRules,
  setRuleEnabled,
  updateIntegration,
} from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canEditRules } from "@/lib/permissions";

import { SeverityBadge } from "./badges";
import { cardClass, errorText, selectClass, tableHeadClass, useOrgRole } from "./shared";

export function RulesPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = canEditRules(useOrgRole(organizationId));
  const rules = useQuery({
    queryKey: ["rules", organizationId, token],
    queryFn: () => listRules(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setRuleEnabled(token!, organizationId, id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rules", organizationId] }),
  });
  const rows = rules.data ?? [];

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Rules</h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            Rules watch device data and open{" "}
            <Link className="underline" to={`/organizations/${organizationId}/alerts`}>
              alerts
            </Link>
            , call webhooks or restart devices when a condition holds. Every save keeps the previous
            version.
          </p>
        </div>
        {canEdit && (
          <Button asChild>
            <Link to={`/organizations/${organizationId}/rules/new`}>New rule</Link>
          </Button>
        )}
      </div>

      {(rules.isError || toggle.isError) && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(rules.error ?? toggle.error, "Could not load rules.")}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className={tableHeadClass}>
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Applies to</th>
              <th className="px-4 py-3 font-semibold">Severity</th>
              <th className="px-4 py-3 font-semibold">Version</th>
              <th className="px-4 py-3 font-semibold">Enabled</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((rule) => (
              <tr key={rule.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <Link
                    className="font-medium hover:underline"
                    to={`/organizations/${organizationId}/rules/${rule.id}`}
                  >
                    {rule.name}
                  </Link>
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {rule.scope_type.replace("_", " ")}
                </td>
                <td className="px-4 py-3">
                  <SeverityBadge severity={rule.severity} />
                </td>
                <td className="px-4 py-3">{rule.current_version}</td>
                <td className="px-4 py-3">
                  <label className="inline-flex items-center gap-2">
                    <input
                      type="checkbox"
                      aria-label={`Enable ${rule.name}`}
                      checked={rule.enabled}
                      disabled={!canEdit || toggle.isPending}
                      onChange={(event) =>
                        toggle.mutate({ id: rule.id, enabled: event.target.checked })
                      }
                    />
                    {rule.enabled ? "On" : "Off"}
                  </label>
                </td>
              </tr>
            ))}
            {!rules.isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-muted-foreground">
                  No rules yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Integrations organizationId={organizationId} token={token} canEdit={canEdit} />
    </section>
  );
}

function Integrations({
  organizationId,
  token,
  canEdit,
}: {
  organizationId: string;
  token: string | null;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<"webhook" | "slack">("webhook");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const integrations = useQuery({
    queryKey: ["notification-integrations", organizationId, token],
    queryFn: () => listIntegrations(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["notification-integrations", organizationId] });
  const create = useMutation({
    mutationFn: () =>
      createIntegration(token!, organizationId, {
        kind,
        name: name.trim(),
        url: url.trim(),
        secret: secret || undefined,
      }),
    onSuccess: () => {
      setName("");
      setUrl("");
      setSecret("");
      refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateIntegration(token!, organizationId, id, { enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteIntegration(token!, organizationId, id),
    onSuccess: refresh,
  });
  const error = create.error ?? toggle.error ?? remove.error;

  return (
    <div className={`space-y-4 ${cardClass}`}>
      <div>
        <h2 className="text-lg font-semibold">Webhook targets</h2>
        <p className="text-sm text-muted-foreground">
          Where webhook and notification actions deliver. With a secret, each request carries an{" "}
          <code>X-MeteorCloud-Signature</code> HMAC header. Slack targets get a short text message.
        </p>
      </div>
      <ul className="divide-y divide-border text-sm">
        {(integrations.data ?? []).map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              <span className="font-medium">{item.name}</span>{" "}
              <span className="text-muted-foreground">
                {item.kind} · {item.url}
                {item.has_secret && " · signed"}
              </span>
              <span className="ml-2 font-mono text-xs text-muted-foreground">{item.id}</span>
            </span>
            {canEdit && (
              <span className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => toggle.mutate({ id: item.id, enabled: !item.enabled })}
                >
                  {item.enabled ? "Disable" : "Enable"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove.mutate(item.id)}>
                  Delete
                </Button>
              </span>
            )}
          </li>
        ))}
        {integrations.data?.length === 0 && (
          <li className="py-2 text-muted-foreground">No targets yet.</li>
        )}
      </ul>
      {canEdit && (
        <form
          className="grid gap-3 md:grid-cols-5"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div>
            <Label htmlFor="integration-kind">Kind</Label>
            <select
              id="integration-kind"
              className={selectClass}
              value={kind}
              onChange={(event) => setKind(event.target.value as "webhook" | "slack")}
            >
              <option value="webhook">Webhook (JSON)</option>
              <option value="slack">Slack-compatible</option>
            </select>
          </div>
          <div>
            <Label htmlFor="integration-name">Name</Label>
            <Input
              id="integration-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={120}
            />
          </div>
          <div>
            <Label htmlFor="integration-url">URL</Label>
            <Input
              id="integration-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          </div>
          <div>
            <Label htmlFor="integration-secret">Signing secret</Label>
            <Input
              id="integration-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="Optional, 16+ characters"
              autoComplete="new-password"
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={create.isPending}>
              Add target
            </Button>
          </div>
        </form>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(error, "Could not change the webhook targets.")}
        </p>
      )}
    </div>
  );
}
