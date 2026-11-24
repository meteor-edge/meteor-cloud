import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/http";
import { listMetrics, type Metric } from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { useOrganizationContext } from "@/context/OrganizationContext";

export function useOrgRole(organizationId: string): string | undefined {
  const { organizations } = useOrganizationContext();
  return organizations.find((organization) => organization.id === organizationId)
    ?.current_user_role;
}

export function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** The organization's metric catalog, and the same by field. */
export function useMetrics(organizationId: string) {
  const { token } = useAuth();
  const query = useQuery({
    queryKey: ["rule-metrics", organizationId, token],
    queryFn: () => listMetrics(token!, organizationId),
    enabled: Boolean(token && organizationId),
    staleTime: 60_000,
  });
  const list = query.data;
  const byField = useMemo(
    () => Object.fromEntries((list ?? []).map((m) => [m.field, m])) as Record<string, Metric>,
    [list],
  );
  return { ...query, list: list ?? [], byField };
}

export function formatNumber(value: number, unit?: string): string {
  if (unit === "B" || unit === "B/s") {
    const units = ["", "k", "M", "G", "T"];
    let v = value;
    let i = 0;
    while (Math.abs(v) >= 1000 && i < units.length - 1) {
      v /= 1000;
      i++;
    }
    return `${Number(v.toFixed(1))} ${units[i]}${unit}`;
  }
  const text = String(Number(value.toFixed(Math.abs(value) < 10 ? 2 : 1)));
  return unit ? `${text}${unit === "%" ? "" : " "}${unit}` : text;
}

const AGGREGATE = /^([a-z]+)\((.+)\)$/;

/** "cpu_percent" or "avg(cpu_percent)" with its reading, readable. */
export function formatReadings(
  values: Record<string, number> | undefined,
  metrics: Record<string, Metric>,
): string {
  return Object.entries(values ?? {})
    .map(([key, value]) => {
      const [, fn, field] = AGGREGATE.exec(key) ?? [null, "", key];
      const metric = metrics[field];
      return `${fn ? `${fn} ` : ""}${metric?.label ?? field} ${formatNumber(value, metric?.unit)}`;
    })
    .join(", ");
}

export function useDebounced<T>(value: T, ms = 400): T {
  const [settled, setSettled] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(JSON.parse(key) as T), ms);
    return () => clearTimeout(timer);
  }, [key, ms]);
  return settled;
}

export const selectClass =
  "mt-1 block h-10 w-full rounded-md border border-input bg-background px-3 text-sm";
export const cardClass = "rounded-lg border border-border bg-card p-5 shadow-glow";
export const tableHeadClass =
  "border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground";
