import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getAlertSummary, listRules, setRuleEnabled } from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { canEditRules } from "@/lib/permissions";

import { SeverityBadge } from "./badges";
import { fromDefinition, summary as describe } from "./form";
import { errorText, tableHeadClass, useMetrics, useOrgRole } from "./shared";

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
  const metrics = useMetrics(organizationId);
  const alerts = useQuery({
    queryKey: ["alert-summary", organizationId, token],
    queryFn: () => getAlertSummary(token!, organizationId),
    enabled: Boolean(token && organizationId),
    refetchInterval: 15_000,
  });
  const sentence = (definition: Record<string, unknown>) => {
    const form = fromDefinition(definition);
    return form ? describe(form, metrics.byField) : "";
  };
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
            , notify your{" "}
            <Link className="underline" to={`/organizations/${organizationId}/channels`}>
              channels
            </Link>{" "}
            or restart devices when a condition holds. Every save keeps the previous version.
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
              <th className="px-4 py-3 font-semibold">Active alerts</th>
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
                  <div className="text-xs text-muted-foreground">{sentence(rule.definition)}</div>
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {rule.scope_type.replace("_", " ")}
                </td>
                <td className="px-4 py-3">
                  <SeverityBadge severity={rule.severity} />
                </td>
                <td className="px-4 py-3">
                  {alerts.data?.by_rule[rule.id] ? (
                    <Link
                      className="font-semibold text-destructive hover:underline"
                      to={`/organizations/${organizationId}/alerts?rule_id=${rule.id}`}
                    >
                      {alerts.data.by_rule[rule.id]}
                    </Link>
                  ) : (
                    "0"
                  )}
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
                <td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">
                  No rules yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
