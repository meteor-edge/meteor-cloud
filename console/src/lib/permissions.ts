export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export type OrganizationRole = "owner" | "admin" | "operator" | "developer" | "viewer";

export function canManageMembers(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

/** Teams group members; they grant no permissions. */
export function canReadTeams(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "operator" || role === "developer";
}

export function canManageTeams(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canUpdateOrganization(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canDeleteOrganization(role: string | undefined): boolean {
  return role === "owner";
}

/** Device operate / enroll. Prefer /me/access when available. */
export function canManageFleet(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "operator";
}

export function canDeleteDevices(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canManageArtifacts(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "developer";
}

export function canManageDeviceTypes(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "developer";
}

export function canManageDeviceGroups(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canManageEnrollmentKeys(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function assignableRoles(actorRole: string | undefined): OrganizationRole[] {
  if (actorRole === "owner") {
    return ["owner", "admin", "operator", "developer", "viewer"];
  }
  if (actorRole === "admin") {
    return ["operator", "developer", "viewer"];
  }
  return [];
}

export function roleLabel(role: string | undefined): string {
  switch (role) {
    case "owner":
      return "Owner";
    case "admin":
      return "Admin";
    case "operator":
      return "Operator";
    case "developer":
      return "Developer";
    case "viewer":
      return "Viewer";
    case "member":
      return "Operator";
    default:
      return role ?? "—";
  }
}

export function roleDescription(role: string | undefined): string {
  switch (role) {
    case "owner":
      return "Full control, including deleting the organization and assigning other owners.";
    case "admin":
      return "Manages people and day-to-day operations. Cannot delete the organization.";
    case "operator":
    case "member":
      return "Runs devices and deployments in the places you assign. Cannot manage people.";
    case "developer":
      return "Manages device types, artifacts, and deployments. Limited control of live devices.";
    case "viewer":
      return "Can look around. Cannot change devices, people, or settings.";
    default:
      return "";
  }
}
