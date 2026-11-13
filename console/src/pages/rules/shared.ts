import { ApiError } from "@/api/http";
import { useOrganizationContext } from "@/context/OrganizationContext";

export function useOrgRole(organizationId: string): string | undefined {
  const { organizations } = useOrganizationContext();
  return organizations.find((organization) => organization.id === organizationId)
    ?.current_user_role;
}

export function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function formatValues(values: Record<string, number> | undefined): string {
  if (!values) return "";
  return Object.entries(values)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ");
}

export const selectClass =
  "mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm";
export const cardClass = "rounded-lg border border-border bg-card p-5 shadow-glow";
export const tableHeadClass =
  "border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground";
