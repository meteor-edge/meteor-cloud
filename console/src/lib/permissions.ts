export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function canManageMembers(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canUpdateOrganization(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canDeleteOrganization(role: string | undefined): boolean {
  return role === "owner";
}

export function canManageFleet(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function assignableRoles(
  actorRole: string | undefined,
): Array<"owner" | "admin" | "member" | "viewer"> {
  if (actorRole === "owner") {
    return ["owner", "admin", "member", "viewer"];
  }
  if (actorRole === "admin") {
    return ["member", "viewer"];
  }
  return [];
}
