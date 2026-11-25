import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  acknowledgeAlert,
  createSilence,
  deleteSilence,
  getAlertSummary,
  listAlerts,
  listRules,
  listSilences,
  type AlertFilters,
  type AlertStatus,
  type Severity,
} from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canActOnAlerts } from "@/lib/permissions";

import { SeverityBadge, StatusBadge } from "./badges";
import { DeviceName, DevicePicker } from "./DevicePicker";
import {
  cardClass,
  errorText,
  formatReadings,
  selectClass,
  tableHeadClass,
  useDebounced,
  useMetrics,
  useOrgRole,
} from "./shared";

const PAGE_SIZE = 50;

type View = "active" | AlertStatus | "all";
type Cursor = { before: string; before_id: string };

type Chosen = {
  severity: string;
  ruleId: string;
  deviceId: string;
  metric: string;
  search: string;
};

function filtersFor(view: View, chosen: Chosen): AlertFilters {
  const filters: AlertFilters = { limit: PAGE_SIZE };
  if (view === "active") filters.active = true;
  else if (view !== "all") filters.status = view;
  if (chosen.severity) filters.severity = chosen.severity as Severity;
  if (chosen.ruleId) filters.rule_id = chosen.ruleId;
  if (chosen.deviceId) filters.device_id = chosen.deviceId;
  if (chosen.metric) filters.metric = chosen.metric;
  if (chosen.search) filters.search = chosen.search;
  return filters;
}

export function AlertsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const canAct = canActOnAlerts(useOrgRole(organizationId));
  const [view, setView] = useState<View>("active");
  const [severity, setSeverity] = useState("");
  const [cursors, setCursors] = useState<Cursor[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const ruleId = searchParams.get("rule_id") ?? "";
  const deviceId = searchParams.get("device_id") ?? "";
  const [metric, setMetric] = useState("");
  const [search, setSearch] = useState("");
  const term = useDebounced(search.trim(), 300);
  const metrics = useMetrics(organizationId);
  const setParam = (key: string, value: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
    setCursors([]);
  };
  const filters = {
    ...filtersFor(view, { severity, ruleId, deviceId, metric, search: term }),
    ...(cursors[cursors.length - 1] ?? {}),
  };

  const alerts = useQuery({
    queryKey: ["alerts", organizationId, token, filters],
    queryFn: () => listAlerts(token!, organizationId, filters),
    enabled: Boolean(token && organizationId),
    refetchInterval: 15_000,
  });
  const summary = useQuery({
    queryKey: ["alert-summary", organizationId, token],
    queryFn: () => getAlertSummary(token!, organizationId),
    enabled: Boolean(token && organizationId),
    refetchInterval: 15_000,
  });
  const rules = useQuery({
    queryKey: ["rules", organizationId, token],
    queryFn: () => listRules(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const silences = useQuery({
    queryKey: ["alert-silences", organizationId, token],
    queryFn: () => listSilences(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["alerts", organizationId] });
    queryClient.invalidateQueries({ queryKey: ["alert-silences", organizationId] });
    queryClient.invalidateQueries({ queryKey: ["alert-summary", organizationId] });
  };
  const hush = useMutation({
    mutationFn: ({ rule_id, device_id }: { rule_id: string; device_id: string }) =>
      createSilence(token!, organizationId, {
        rule_id,
        device_id,
        ends_at: new Date(Date.now() + 2 * 3600_000).toISOString(),
      }),
    onSuccess: refresh,
  });
  const ack = useMutation({
    mutationFn: (id: string) => acknowledgeAlert(token!, organizationId, id),
    onSuccess: refresh,
  });

  const rows = alerts.data ?? [];
  const last = rows[rows.length - 1];

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Alerts</h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            Opened and resolved by your{" "}
            <Link className="underline" to={`/organizations/${organizationId}/rules`}>
              rules
            </Link>
            . Acknowledging or silencing an alert never changes what the rule evaluates.
          </p>
        </div>
      </div>

      {summary.data && (
        <div
          className="grid grid-cols-2 gap-3 md:grid-cols-5"
          aria-label="Active alerts"
          role="group"
        >
          {(
            [
              ["Firing", summary.data.firing, () => setView("firing")],
              ["Acknowledged", summary.data.acknowledged, () => setView("acknowledged")],
              [
                "Critical",
                summary.data.by_severity.critical ?? 0,
                () => {
                  setView("active");
                  setSeverity("critical");
                },
              ],
              [
                "Warning",
                summary.data.by_severity.warning ?? 0,
                () => {
                  setView("active");
                  setSeverity("warning");
                },
              ],
            ] as [string, number, () => void][]
          ).map(([label, count, show]) => (
            <button
              key={label}
              type="button"
              className={`text-left hover:bg-secondary ${cardClass}`}
              onClick={() => {
                setSeverity("");
                setCursors([]);
                show();
              }}
            >
              <div className="text-2xl font-semibold">{count}</div>
              <div className="text-sm text-muted-foreground">{label}</div>
            </button>
          ))}
          <div className={cardClass}>
            <div className="text-2xl font-semibold">{summary.data.devices}</div>
            <div className="text-sm text-muted-foreground">Devices affected</div>
          </div>
        </div>
      )}

      <div className={`grid gap-3 md:grid-cols-3 ${cardClass}`}>
        <div>
          <Label htmlFor="alert-search">Search</Label>
          <Input
            id="alert-search"
            className="mt-1"
            value={search}
            placeholder="Device or rule name"
            onChange={(event) => {
              setSearch(event.target.value);
              setCursors([]);
            }}
          />
        </div>
        <div>
          <Label htmlFor="alert-rule">Rule</Label>
          <select
            id="alert-rule"
            className={selectClass}
            value={ruleId}
            onChange={(event) => setParam("rule_id", event.target.value)}
          >
            <option value="">Any</option>
            {ruleId && !(rules.data ?? []).some((r) => r.id === ruleId) && (
              <option value={ruleId}>{ruleId.slice(0, 8)}</option>
            )}
            {(rules.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="alert-metric">Metric</Label>
          <select
            id="alert-metric"
            className={selectClass}
            value={metric}
            onChange={(event) => {
              setMetric(event.target.value);
              setCursors([]);
            }}
          >
            <option value="">Any</option>
            {metrics.list.map((m) => (
              <option key={m.field} value={m.field}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="alert-view">Show</Label>
          <select
            id="alert-view"
            className={selectClass}
            value={view}
            onChange={(event) => {
              setView(event.target.value as View);
              setCursors([]);
            }}
          >
            <option value="active">Active (firing or acknowledged)</option>
            <option value="firing">Firing</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option>
            <option value="all">All</option>
          </select>
        </div>
        <div>
          <Label htmlFor="alert-severity">Severity</Label>
          <select
            id="alert-severity"
            className={selectClass}
            value={severity}
            onChange={(event) => {
              setSeverity(event.target.value);
              setCursors([]);
            }}
          >
            <option value="">Any</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>
        <div>
          <Label>Device</Label>
          <div className="mt-1">
            <DevicePicker
              organizationId={organizationId}
              label="Device"
              single
              value={deviceId ? [deviceId] : []}
              onChange={(ids) => setParam("device_id", ids[0] ?? "")}
            />
          </div>
        </div>
      </div>

      {(alerts.isError || ack.isError || hush.isError) && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(alerts.error ?? ack.error ?? hush.error, "Could not load alerts.")}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className={tableHeadClass}>
            <tr>
              <th className="px-4 py-3 font-semibold">Fired</th>
              <th className="px-4 py-3 font-semibold">Rule</th>
              <th className="px-4 py-3 font-semibold">Device</th>
              <th className="px-4 py-3 font-semibold">Severity</th>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Values</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {rows.map((alert) => (
              <tr key={alert.id} className="border-b border-border align-top last:border-0">
                <td className="whitespace-nowrap px-4 py-3" title={alert.fired_at}>
                  {new Date(alert.fired_at).toLocaleString()}
                  {alert.resolved_at && (
                    <div className="text-xs text-muted-foreground">
                      resolved {new Date(alert.resolved_at).toLocaleString()}
                      {alert.resolve_reason &&
                        alert.resolve_reason !== "condition_cleared" &&
                        ` (${alert.resolve_reason})`}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Link
                    className="font-medium hover:underline"
                    to={`/organizations/${organizationId}/rules/${alert.rule_id}`}
                  >
                    {alert.rule_name}
                  </Link>
                  <div className="text-xs text-muted-foreground">version {alert.rule_version}</div>
                </td>
                <td className="px-4 py-3">
                  <Link
                    className="hover:underline"
                    to={`/organizations/${organizationId}/devices/${alert.device_id}`}
                  >
                    {alert.device_name || alert.device_id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <SeverityBadge severity={alert.severity} />
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={alert.status} silenced={alert.silenced} />
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatReadings(alert.trigger_values, metrics.byField)}
                </td>
                <td className="px-4 py-3 text-right">
                  {canAct && alert.status === "firing" && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={ack.isPending}
                      onClick={() => ack.mutate(alert.id)}
                    >
                      Acknowledge
                    </Button>
                  )}
                  {canAct && alert.status !== "resolved" && !alert.silenced && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={hush.isPending}
                      title="Hold notifications of this rule for this device for 2 hours"
                      onClick={() =>
                        hush.mutate({ rule_id: alert.rule_id, device_id: alert.device_id })
                      }
                    >
                      Silence 2 h
                    </Button>
                  )}
                </td>
              </tr>
            ))}
            {!alerts.isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-muted-foreground">
                  No alerts match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between">
        <Button
          type="button"
          variant="outline"
          disabled={cursors.length === 0}
          onClick={() => setCursors((current) => current.slice(0, -1))}
        >
          Newer
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!last || rows.length < PAGE_SIZE}
          onClick={() =>
            last &&
            setCursors((current) => [...current, { before: last.fired_at, before_id: last.id }])
          }
        >
          Older
        </Button>
      </div>

      <Silences
        organizationId={organizationId}
        token={token}
        canAct={canAct}
        silences={silences.data ?? []}
        rules={rules.data ?? []}
        onChange={refresh}
      />
    </section>
  );
}

function Silences({
  organizationId,
  token,
  canAct,
  silences,
  rules,
  onChange,
}: {
  organizationId: string;
  token: string | null;
  canAct: boolean;
  rules: { id: string; name: string }[];
  silences: {
    id: string;
    rule_id: string | null;
    device_id: string | null;
    comment: string | null;
    ends_at: string;
  }[];
  onChange: () => void;
}) {
  const [hours, setHours] = useState("2");
  const [ruleId, setRuleId] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [comment, setComment] = useState("");
  const create = useMutation({
    mutationFn: () =>
      createSilence(token!, organizationId, {
        rule_id: ruleId.trim() || undefined,
        device_id: deviceId.trim() || undefined,
        comment: comment.trim() || undefined,
        ends_at: new Date(Date.now() + Number(hours) * 3600_000).toISOString(),
      }),
    onSuccess: () => {
      setRuleId("");
      setDeviceId("");
      setComment("");
      onChange();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteSilence(token!, organizationId, id),
    onSuccess: onChange,
  });

  return (
    <div className={`space-y-4 ${cardClass}`}>
      <div>
        <h2 className="text-lg font-semibold">Silences</h2>
        <p className="text-sm text-muted-foreground">
          A silence holds webhooks and notifications for matching alerts until it ends. Alerts are
          still recorded. Leave rule and device empty to silence everything.
        </p>
      </div>
      {silences.length > 0 && (
        <ul className="divide-y divide-border text-sm">
          {silences.map((silence) => (
            <li key={silence.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {silence.rule_id
                  ? (rules.find((r) => r.id === silence.rule_id)?.name ??
                    `Rule ${silence.rule_id.slice(0, 8)}`)
                  : "All rules"}{" "}
                ·{" "}
                {silence.device_id ? (
                  <DeviceName organizationId={organizationId} id={silence.device_id} />
                ) : (
                  "all devices"
                )}{" "}
                · until {new Date(silence.ends_at).toLocaleString()}
                {silence.comment && (
                  <span className="text-muted-foreground"> · {silence.comment}</span>
                )}
              </span>
              {canAct && (
                <Button size="sm" variant="ghost" onClick={() => remove.mutate(silence.id)}>
                  End now
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canAct && (
        <form
          className="grid gap-3 md:grid-cols-5"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div>
            <Label htmlFor="silence-hours">For</Label>
            <select
              id="silence-hours"
              className={selectClass}
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            >
              <option value="1">1 hour</option>
              <option value="2">2 hours</option>
              <option value="8">8 hours</option>
              <option value="24">1 day</option>
              <option value="168">1 week</option>
            </select>
          </div>
          <div>
            <Label htmlFor="silence-rule">Rule</Label>
            <select
              id="silence-rule"
              className={selectClass}
              value={ruleId}
              onChange={(e) => setRuleId(e.target.value)}
            >
              <option value="">Any</option>
              {rules.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label>Device</Label>
            <div className="mt-1">
              <DevicePicker
                organizationId={organizationId}
                label="Device to silence"
                single
                value={deviceId ? [deviceId] : []}
                onChange={(ids) => setDeviceId(ids[0] ?? "")}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="silence-comment">Comment</Label>
            <Input
              id="silence-comment"
              className="mt-1"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="flex items-start pt-6">
            <Button type="submit" disabled={create.isPending}>
              Silence
            </Button>
          </div>
          {create.isError && (
            <p className="text-sm text-destructive md:col-span-5" role="alert">
              {errorText(create.error, "Could not create the silence.")}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
