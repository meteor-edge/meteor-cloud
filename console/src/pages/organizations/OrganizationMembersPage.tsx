import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
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
import { assignableRoles, canManageMembers } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

export function OrganizationMembersPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrganizationRole>("member");
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

  const actorRole = orgQuery.data?.current_user_role;
  const canManage = canManageMembers(actorRole);
  const roles = useMemo(() => assignableRoles(actorRole), [actorRole]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["members", organizationId] });
    await queryClient.invalidateQueries({ queryKey: ["organization", organizationId] });
    await queryClient.invalidateQueries({ queryKey: ["organizations"] });
  };

  const addMutation = useMutation({
    mutationFn: () => addMember(token!, organizationId, { email, role }),
    onSuccess: async () => {
      setEmail("");
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
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update role.");
    }
  }

  async function onRemove(membershipId: string) {
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
    return <p className="text-red-700">Organization was not found.</p>;
  }

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Members</h1>
          <p className="mt-2 text-muted-foreground">{orgQuery.data.name}</p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}`}>Back to overview</Link>
        </Button>
      </div>

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-white/80 p-5 shadow-sm md:grid-cols-[1fr_160px_auto]"
          onSubmit={onAdd}
        >
          <div>
            <Label htmlFor="member-email">Add existing user by email</Label>
            <Input
              id="member-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div>
            <Label htmlFor="member-role">Role</Label>
            <select
              id="member-role"
              className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
              value={role}
              onChange={(event) => setRole(event.target.value as OrganizationRole)}
            >
              {roles.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={addMutation.isPending}>
              Add member
            </Button>
          </div>
        </form>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="overflow-hidden rounded-lg border border-border bg-white/80 shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Email</th>
              <th className="px-4 py-3 font-semibold">Role</th>
              <th className="px-4 py-3 font-semibold">Joined</th>
              {canManage && <th className="px-4 py-3 font-semibold">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {(membersQuery.data ?? []).map((member) => {
              const editable =
                canManage &&
                (actorRole === "owner" ||
                  (actorRole === "admin" &&
                    (member.role === "member" || member.role === "viewer")));
              return (
                <tr key={member.id} className="border-b border-border/70">
                  <td className="px-4 py-3 font-medium">{member.full_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{member.email}</td>
                  <td className="px-4 py-3">
                    {editable ? (
                      <select
                        className="rounded-md border border-input bg-white px-2 py-1"
                        value={member.role}
                        onChange={(event) =>
                          onRoleChange(member.id, event.target.value as OrganizationRole)
                        }
                      >
                        {roles.map((item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="capitalize">{member.role}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDateTime(member.created_at)}
                  </td>
                  {canManage && (
                    <td className="px-4 py-3">
                      {editable && (
                        <Button variant="ghost" size="sm" onClick={() => onRemove(member.id)}>
                          Remove
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
