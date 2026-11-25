import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { queryLiveDevices, type LiveQuery } from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canEditRules } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { SeverityBadge } from "./badges";
import { ConditionRow } from "./ConditionRow";
import { newCondition, OPERATORS, type FormCondition } from "./form";
import {
  cardClass,
  errorText,
  formatNumber,
  selectClass,
  tableHeadClass,
  useDebounced,
  useMetrics,
  useOrgRole,
} from "./shared";

const PAGE = 100;
const COLUMNS = ["cpu_percent", "memory_percent", "disk_percent", "temperature_c"];

type Filter = {
  conditions: FormCondition[];
  match: "all" | "any";
  state: NonNullable<LiveQuery["state"]>;
  alerts: NonNullable<LiveQuery["alerts"]>;
};
const EVERYTHING: Filter = { conditions: [], match: "all", state: "any", alerts: "any" };
const over = (field: string, value: string): FormCondition => newCondition({ field, value });

const PRESETS: { label: string; filter: Partial<Filter> }[] = [
  { label: "All devices", filter: {} },
  { label: "CPU > 80%", filter: { conditions: [over("cpu_percent", "80")] } },
  { label: "Memory > 80%", filter: { conditions: [over("memory_percent", "80")] } },
  {
    label: "CPU or memory > 80%",
    filter: {
      match: "any",
      conditions: [over("cpu_percent", "80"), over("memory_percent", "80")],
    },
  },
  { label: "Disk > 90%", filter: { conditions: [over("disk_percent", "90")] } },
  { label: "Hot (> 75 °C)", filter: { conditions: [over("temperature_c", "75")] } },
  { label: "With active alerts", filter: { alerts: "active" } },
  { label: "Offline", filter: { state: "offline" } },
];

const valid = (c: FormCondition) =>
  c.field !== "" &&
  c.value.trim() !== "" &&
  (c.operator !== "between" || (c.max.trim() !== "" && Number(c.max) >= Number(c.value)));

export function DeviceHealthPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const canEdit = canEditRules(useOrgRole(organizationId));
  const metrics = useMetrics(organizationId);
  const [filter, setFilter] = useState<Filter>(EVERYTHING);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const change = (next: Partial<Filter>) => {
    setFilter((current) => ({ ...current, ...next }));
    setOffset(0);
  };
  const conditions = filter.conditions.filter(valid);
  const body = useDebounced<LiveQuery>(
    {
      conditions: conditions.map((c) => ({
        field: c.field,
        operator: c.operator,
        value: Number(c.value),
        ...(c.operator === "between" ? { max: Number(c.max) } : {}),
      })),
      match: filter.match,
      state: filter.state,
      alerts: filter.alerts,
      search: search.trim(),
      limit: PAGE,
      offset,
    },
    250,
  );
  const live = useQuery({
    queryKey: ["live-devices", organizationId, token, body],
    queryFn: () => queryLiveDevices(token!, organizationId, body),
    enabled: Boolean(token && organizationId),
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });
  const columns = [...new Set([...COLUMNS, ...conditions.map((c) => c.field)])];
  const breached = (field: string, value: number) =>
    conditions.some((c) => {
      if (c.field !== field) return false;
      const v = Number(c.value);
      switch (c.operator) {
        case "gt":
          return value > v;
        case "gte":
          return value >= v;
        case "lt":
          return value < v;
        case "lte":
          return value <= v;
        case "eq":
          return value === v;
        case "ne":
          return value !== v;
        default:
          return value >= v && value <= Number(c.max);
      }
    });
  const first = conditions[0];
  const ruleLink = `/organizations/${organizationId}/rules/new?${new URLSearchParams(
    conditions.flatMap((c) => [["c", [c.field, c.operator, c.value, c.max].join(":")]]),
  )}${conditions.length > 1 ? `&match=${filter.match}` : ""}`;

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Device health</h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Latest readings of your devices. Filter by any metric, with or without a rule, and turn a
          filter into a rule.
        </p>
      </div>

      {/* Cards blur what is behind them, which makes each a stacking context: lift this one
          so the metric list opens over the table below. */}
      <div className={`relative z-20 space-y-4 ${cardClass}`}>
        <div className="flex flex-wrap gap-2" aria-label="Quick filters" role="group">
          {PRESETS.map((preset) => {
            const target = { ...EVERYTHING, ...preset.filter };
            const active = JSON.stringify(target) === JSON.stringify(filter);
            return (
              <button
                key={preset.label}
                type="button"
                aria-pressed={active}
                className={cn(
                  "rounded-full border px-3 py-1 text-sm",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:bg-secondary",
                )}
                onClick={() => change(target)}
              >
                {preset.label}
              </button>
            );
          })}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <Label htmlFor="health-search">Device name</Label>
            <Input
              id="health-search"
              className="mt-1"
              value={search}
              placeholder="Search…"
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          <div>
            <Label htmlFor="health-state">Connection</Label>
            <select
              id="health-state"
              className={selectClass}
              value={filter.state}
              onChange={(e) => change({ state: e.target.value as Filter["state"] })}
            >
              <option value="any">Any</option>
              <option value="online">Online</option>
              <option value="offline">Offline</option>
            </select>
          </div>
          <div>
            <Label htmlFor="health-alerts">Alerts</Label>
            <select
              id="health-alerts"
              className={selectClass}
              value={filter.alerts}
              onChange={(e) => change({ alerts: e.target.value as Filter["alerts"] })}
            >
              <option value="any">Any</option>
              <option value="active">With active alerts</option>
              <option value="none">Without active alerts</option>
            </select>
          </div>
        </div>
        {filter.conditions.length > 1 && (
          <select
            aria-label="Match"
            className={cn(selectClass, "max-w-64")}
            value={filter.match}
            onChange={(e) => change({ match: e.target.value as Filter["match"] })}
          >
            <option value="all">All conditions are true</option>
            <option value="any">Any condition is true</option>
          </select>
        )}
        {filter.conditions.map((condition, index) => (
          <ConditionRow
            key={index}
            index={index}
            condition={condition}
            metrics={metrics.list}
            kinds={false}
            onChange={(next) =>
              change({ conditions: filter.conditions.map((c, i) => (i === index ? next : c)) })
            }
            onRemove={() => change({ conditions: filter.conditions.filter((_, i) => i !== index) })}
          />
        ))}
        <div className="flex flex-wrap gap-2">
          {filter.conditions.length < 10 && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => change({ conditions: [...filter.conditions, newCondition()] })}
            >
              Add metric filter
            </Button>
          )}
          {canEdit && first && (
            <Button size="sm" variant="secondary" asChild>
              <Link to={ruleLink}>Create rule from this filter</Link>
            </Button>
          )}
        </div>
      </div>

      {live.isError && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(live.error, "Could not load devices.")}
        </p>
      )}
      {live.data && (
        <p className="text-sm text-muted-foreground" role="status">
          {live.data.matching} of {live.data.total} device{live.data.total === 1 ? "" : "s"} match
          {conditions.length > 0 && (
            <>
              {": "}
              {conditions
                .map((c) => {
                  const metric = metrics.byField[c.field];
                  const symbol = OPERATORS.find((o) => o.id === c.operator)?.symbol;
                  const limit =
                    c.operator === "between"
                      ? `${formatNumber(Number(c.value), metric?.unit)} and ${formatNumber(Number(c.max), metric?.unit)}`
                      : formatNumber(Number(c.value), metric?.unit);
                  return `${metric?.label ?? c.field} ${symbol} ${limit}`;
                })
                .join(filter.match === "all" ? " and " : " or ")}
              . Only readings from the last 5 minutes count.
            </>
          )}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className={tableHeadClass}>
            <tr>
              <th className="px-4 py-3 font-semibold">Device</th>
              {columns.map((field) => (
                <th key={field} className="px-4 py-3 font-semibold">
                  {metrics.byField[field]?.label ?? field}
                </th>
              ))}
              <th className="px-4 py-3 font-semibold">Last reading</th>
              <th className="px-4 py-3 font-semibold">Active alerts</th>
            </tr>
          </thead>
          <tbody>
            {(live.data?.devices ?? []).map((device) => (
              <tr key={device.id} className="border-b border-border align-top last:border-0">
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-2">
                    <span
                      className={cn(
                        "h-2 w-2 rounded-full",
                        device.online ? "bg-emerald-500" : "bg-muted-foreground",
                      )}
                      role="img"
                      aria-label={device.online ? "online" : "offline"}
                    />
                    <Link
                      className="font-medium hover:underline"
                      to={`/organizations/${organizationId}/devices/${device.id}`}
                    >
                      {device.name}
                    </Link>
                  </span>
                </td>
                {columns.map((field) => {
                  const value = device.metrics[field];
                  return (
                    <td
                      key={field}
                      className={cn(
                        "whitespace-nowrap px-4 py-3 tabular-nums",
                        !device.fresh && "text-muted-foreground",
                        value !== undefined &&
                          device.fresh &&
                          breached(field, value) &&
                          "font-semibold text-destructive",
                      )}
                    >
                      {value === undefined
                        ? "—"
                        : formatNumber(value, metrics.byField[field]?.unit)}
                    </td>
                  );
                })}
                <td className="whitespace-nowrap px-4 py-3 text-muted-foreground">
                  {device.metrics_at ? new Date(device.metrics_at).toLocaleString() : "never"}
                </td>
                <td className="space-y-1 px-4 py-3">
                  {device.alerts.map((alert) => (
                    <div key={alert.id} className="flex items-center gap-2">
                      <SeverityBadge severity={alert.severity} />
                      <Link
                        className="hover:underline"
                        to={`/organizations/${organizationId}/alerts?device_id=${device.id}`}
                      >
                        {alert.rule_name}
                      </Link>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
            {live.data && live.data.devices.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length + 3}
                  className="px-4 py-6 text-center text-muted-foreground"
                >
                  No devices match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {live.data && live.data.matching > PAGE && (
        <div className="flex justify-between">
          <Button
            variant="outline"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            disabled={offset + PAGE >= live.data.matching}
            onClick={() => setOffset((o) => o + PAGE)}
          >
            Next
          </Button>
        </div>
      )}
    </section>
  );
}
