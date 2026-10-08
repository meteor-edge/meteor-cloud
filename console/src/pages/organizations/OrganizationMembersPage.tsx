import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import { listDeviceGroups } from "@/api/fleet/deviceGroups";
import {
  addMember,
  getOrganization,
  listMembers,
  removeMember,
  updateMemberRole,
  type OrganizationRole,
} from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  assignableRoles,
  canManageMembers,
  roleDescription,
  roleLabel,
} from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export function OrganizationMembersPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<OrganizationRole>("operator");
  const [scopeOrgWide, setScopeOrgWide] = useState(true);
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [changingRoleId, setChangingRoleId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const membersQuery = useQuery({
    queryKey: ["members", organizationId, token],
    queryFn: () => listMembers(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const actorRole = orgQuery.data?.current_user_role;
  const canManage = canManageMembers(actorRole);
  const roles = useMemo(() => assignableRoles(actorRole), [actorRole]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["members", organizationId] });
    await queryClient.invalidateQueries({ queryKey: ["organization", organizationId] });
    await queryClient.invalidateQueries({ queryKey: ["organizations"] });
  };

  const addMutation = useMutation({
    mutationFn: () =>
      addMember(token!, organizationId, {
        email,
        full_name: fullName.trim() || undefined,
        password: password || undefined,
        role,
        scope: { device_group_ids: scopeOrgWide ? [] : selectedGroups },
      }),
    onSuccess: async () => {
      setEmail("");
      setFullName("");
      setPassword("");
      setRole("operator");
      setScopeOrgWide(true);
      setSelectedGroups([]);
      setAddOpen(false);
      setError(null);
      await invalidate();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not add member.");
    },
  });

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    addMutation.mutate();
  }

  async function onRoleChange(membershipId: string, nextRole: OrganizationRole) {
    setError(null);
    try {
      await updateMemberRole(token!, organizationId, membershipId, nextRole);
      setChangingRoleId(null);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update role.");
    }
  }

  async function onRemove(membershipId: string, name: string) {
    const confirmed = window.confirm(`Remove ${name} from this organization?`);
    if (!confirmed) return;
    setError(null);
    try {
      await removeMember(token!, organizationId, membershipId);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove member.");
    }
  }

  if (orgQuery.isLoading || membersQuery.isLoading) {
    return <p className="text-muted-foreground">Loading members…</p>;
  }

  if (!orgQuery.data) {
    return (
      <p className="bg-ember text-midnight hover:bg-ember hover:text-midnight">
        Organization was not found.
      </p>
    );
  }

  const members = membersQuery.data ?? [];

  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Members</h1>
          <p className="mt-2 max-w-xl text-muted-foreground">
            People in {orgQuery.data.name}. A role decides what they can do. Device groups decide
            which devices they see. Use{" "}
            <Link to={`/organizations/${organizationId}/teams`} className="text-link hover:underline">
              Teams
            </Link>{" "}
            to group them.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canManage && (
            <Button type="button" onClick={() => setAddOpen((open) => !open)}>
              {addOpen ? "Close" : "Add member"}
            </Button>
          )}
          <Button variant="secondary" asChild>
            <Link to={`/organizations/${organizationId}`}>Back to overview</Link>
          </Button>
        </div>
      </div>

      {canManage && addOpen && (
        <form
          className="space-y-5 rounded-lg border border-border bg-card p-5 shadow-glow"
          onSubmit={onAdd}
        >
          <div>
            <h2 className="text-lg font-semibold">Add member</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Add a person by email. If they are new to MeteorCloud, enter their name and a
              temporary password and share it with them directly. People who already sign in
              elsewhere keep their name and password.
            </p>
          </div>

          <div>
            <Label htmlFor="member-email">Email</Label>
            <Input
              id="member-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@company.com"
              required
              autoFocus
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="member-full-name">Full name</Label>
              <Input
                id="member-full-name"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                placeholder="Alex Doe"
                maxLength={255}
                autoComplete="off"
              />
            </div>
            <div>
              <Label htmlFor="member-password">Temporary password</Label>
              <Input
                id="member-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="At least 10 characters"
                minLength={10}
                maxLength={128}
                autoComplete="new-password"
              />
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            Only needed for people new to MeteorCloud.
          </p>

          <RoleChoices
            legend="Role"
            name="new-member-role"
            roles={roles}
            value={role}
            onChange={setRole}
          />

          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-foreground">Where they can work</legend>
            <p className="text-sm text-muted-foreground">
              This limits devices only. Device types and artifacts stay available across the
              organization when the role allows them.
            </p>
            <label className="flex items-start gap-2 text-sm">
              <input
                className="mt-1"
                type="radio"
                name="member-scope"
                checked={scopeOrgWide}
                onChange={() => {
                  setScopeOrgWide(true);
                  setSelectedGroups([]);
                }}
              />
              <span>
                <span className="font-medium text-foreground">Entire organization</span>
                <span className="mt-0.5 block text-muted-foreground">
                  Every device group, and devices that are not in a group.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                className="mt-1"
                type="radio"
                name="member-scope"
                checked={!scopeOrgWide}
                onChange={() => setScopeOrgWide(false)}
              />
              <span>
                <span className="font-medium text-foreground">Specific device groups</span>
                <span className="mt-0.5 block text-muted-foreground">
                  Only devices in the groups you pick.
                </span>
              </span>
            </label>
            {!scopeOrgWide && (
              <div className="grid gap-1 pl-6 sm:grid-cols-2">
                {(groupsQuery.data ?? []).map((group) => {
                  const checked = selectedGroups.includes(group.id);
                  return (
                    <label key={group.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setSelectedGroups((current) =>
                            checked
                              ? current.filter((id) => id !== group.id)
                              : [...current, group.id],
                          )
                        }
                      />
                      {group.name}
                    </label>
                  );
                })}
                {!groupsQuery.data?.length && (
                  <p className="text-sm text-muted-foreground">No device groups yet.</p>
                )}
              </div>
            )}
          </fieldset>

          <Button
            type="submit"
            disabled={addMutation.isPending || (!scopeOrgWide && selectedGroups.length === 0)}
          >
            Add member
          </Button>
        </form>
      )}

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      <ul className="space-y-3">
        {members.map((member) => {
          const editable =
            canManage &&
            (actorRole === "owner" ||
              (actorRole === "admin" &&
                (member.role === "operator" ||
                  member.role === "developer" ||
                  member.role === "viewer")));
          const scopeText =
            member.scope?.mode === "device_groups"
              ? member.scope.device_group_names.join(", ") || "Selected device groups"
              : "Entire organization";
          const changing = changingRoleId === member.id;
          return (
            <li key={member.id} className="rounded-lg border border-border bg-card p-4 shadow-glow">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-medium text-foreground">{member.full_name}</p>
                  <p className="text-sm text-muted-foreground">{member.email}</p>
                </div>
                <p className="text-xs text-muted-foreground">
                  <span className="capitalize">{member.status}</span>
                  {" · Joined "}
                  {formatDateTime(member.created_at)}
                </p>
              </div>

              <div className="mt-3">
                {editable && changing ? (
                  <RoleChoices
                    legend="Change role"
                    name={`member-role-${member.id}`}
                    roles={roles}
                    value={member.role}
                    onChange={(nextRole) => onRoleChange(member.id, nextRole)}
                  />
                ) : (
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {member.role_name || roleLabel(member.role)}
                    </p>
                    <p className="text-sm text-muted-foreground">{roleDescription(member.role)}</p>
                  </div>
                )}
                <p className="mt-2 text-sm text-muted-foreground">Works on: {scopeText}</p>
                {member.teams.length > 0 && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    Teams:{" "}
                    {member.teams.map((team, index) => (
                      <span key={team.id}>
                        {index > 0 && ", "}
                        <Link
                          to={`/organizations/${organizationId}/teams/${team.id}`}
                          className="text-link hover:underline"
                        >
                          {team.name}
                        </Link>
                      </span>
                    ))}
                  </p>
                )}
              </div>

              <div className="mt-3 flex flex-wrap gap-1">
                <Button variant="ghost" size="sm" asChild>
                  <Link to={`/organizations/${organizationId}/members/${member.id}/access`}>
                    View access
                  </Link>
                </Button>
                {editable && (
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    onClick={() => setChangingRoleId(changing ? null : member.id)}
                  >
                    {changing ? "Cancel" : "Change role"}
                  </Button>
                )}
                {editable && (
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    onClick={() => onRemove(member.id, member.full_name)}
                  >
                    Remove
                  </Button>
                )}
              </div>
            </li>
          );
        })}
        {!members.length && (
          <li className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            No members yet.
          </li>
        )}
      </ul>
    </section>
  );
}

function RoleChoices({
  legend,
  name,
  roles,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  roles: OrganizationRole[];
  value: OrganizationRole;
  onChange: (role: OrganizationRole) => void;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-foreground">{legend}</legend>
      <div className="space-y-2">
        {roles.map((item) => {
          const selected = value === item;
          return (
            <label
              key={item}
              className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                selected ? "border-ring bg-background" : "border-border"
              }`}
            >
              <input
                className="mt-1"
                type="radio"
                name={name}
                value={item}
                checked={selected}
                onChange={() => onChange(item)}
              />
              <span>
                <span className="font-medium text-foreground">{roleLabel(item)}</span>
                <span className="mt-0.5 block text-muted-foreground">{roleDescription(item)}</span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
