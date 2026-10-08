import { FormEvent, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import { getOrganization, listMembers } from "@/api/organizations";
import {
  addTeamMember,
  deleteTeam,
  getTeam,
  removeTeamMember,
  updateTeam,
  type Team,
} from "@/api/teams";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageTeams, roleLabel } from "@/lib/permissions";

const SELECT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-field px-3 text-sm text-foreground";

export function TeamDetailPage() {
  const { organizationId = "", teamId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [newMemberId, setNewMemberId] = useState("");

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const teamQuery = useQuery({
    queryKey: ["team", organizationId, teamId, token],
    queryFn: () => getTeam(token!, organizationId, teamId),
    enabled: Boolean(token && organizationId && teamId),
  });

  const canManage = canManageTeams(orgQuery.data?.current_user_role);
  const membersQuery = useQuery({
    queryKey: ["members", organizationId, token],
    queryFn: () => listMembers(token!, organizationId),
    enabled: Boolean(token && organizationId && canManage),
  });

  if (teamQuery.isLoading) {
    return <p className="text-muted-foreground">Loading team…</p>;
  }
  if (!teamQuery.data) {
    return <p className="rounded-md bg-ember px-2 py-1 text-midnight">Team was not found.</p>;
  }
  const team = teamQuery.data;
  const onTeam = new Set(team.members.map((member) => member.membership_id));
  const candidates = (membersQuery.data ?? []).filter((member) => !onTeam.has(member.id));

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["team", organizationId, teamId] }),
      queryClient.invalidateQueries({ queryKey: ["teams", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["members", organizationId] }),
    ]);
  }

  async function run(action: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : fallback);
    }
  }

  async function onAddMember(event: FormEvent) {
    event.preventDefault();
    if (!newMemberId) return;
    await run(() => addTeamMember(token!, organizationId, teamId, newMemberId), "Could not add member.");
    setNewMemberId("");
  }

  async function onDelete() {
    if (!window.confirm(`Delete team "${team.name}"? Its members stay in the organization.`)) {
      return;
    }
    setError(null);
    try {
      await deleteTeam(token!, organizationId, teamId);
      await queryClient.invalidateQueries({ queryKey: ["teams", organizationId] });
      await queryClient.invalidateQueries({ queryKey: ["members", organizationId] });
      navigate(`/organizations/${organizationId}/teams`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete team.");
    }
  }

  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">Team</p>
          <h1 className="text-3xl font-semibold tracking-tight">{team.name}</h1>
          <p className="mt-2 text-muted-foreground">
            {team.description || `${team.member_count} ${team.member_count === 1 ? "member" : "members"}`}
          </p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/teams`}>Back to teams</Link>
        </Button>
      </div>

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow sm:grid-cols-[1fr_auto]"
          onSubmit={onAddMember}
        >
          <div>
            <Label htmlFor="team-add-member">Add a member</Label>
            <select
              id="team-add-member"
              className={SELECT_CLASS}
              value={newMemberId}
              onChange={(event) => setNewMemberId(event.target.value)}
            >
              <option value="">
                {candidates.length ? "Choose a member…" : "Everyone is already on this team"}
              </option>
              {candidates.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.full_name} ({member.email})
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={!newMemberId}>
              Add to team
            </Button>
          </div>
        </form>
      )}

      <ul className="space-y-3">
        {team.members.map((member) => (
          <li
            key={member.membership_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4 shadow-glow"
          >
            <div>
              <p className="font-medium text-foreground">{member.full_name}</p>
              <p className="text-sm text-muted-foreground">
                {member.email} · {member.role_name || roleLabel(member.role)}
              </p>
            </div>
            {canManage && (
              <Button
                variant="ghost"
                size="sm"
                type="button"
                onClick={() =>
                  run(
                    () => removeTeamMember(token!, organizationId, teamId, member.membership_id),
                    "Could not remove member from team.",
                  )
                }
              >
                Remove from team
              </Button>
            )}
          </li>
        ))}
        {!team.members.length && (
          <li className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            No members on this team yet.
          </li>
        )}
      </ul>

      {canManage && (
        <TeamEditForm
          key={team.updated_at}
          team={team}
          onSave={(payload) =>
            run(() => updateTeam(token!, organizationId, teamId, payload), "Could not update team.")
          }
          onDelete={onDelete}
        />
      )}
    </section>
  );
}

function TeamEditForm({
  team,
  onSave,
  onDelete,
}: {
  team: Team;
  onSave: (payload: { name: string; description: string | null }) => Promise<void>;
  onDelete: () => void;
}) {
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    await onSave({ name, description: description || null });
    setSaving(false);
  }

  return (
    <form
      className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
      onSubmit={onSubmit}
    >
      <h2 className="text-lg font-semibold">Edit team</h2>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor="edit-team-name">Name</Label>
          <Input
            id="edit-team-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            required
          />
        </div>
        <div>
          <Label htmlFor="edit-team-description">Description</Label>
          <Input
            id="edit-team-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap justify-between gap-2">
        <Button type="submit" disabled={saving}>
          Save changes
        </Button>
        <Button
          type="button"
          variant="ghost"
          className="bg-ember text-midnight hover:bg-ember hover:text-midnight"
          onClick={onDelete}
        >
          Delete team
        </Button>
      </div>
    </form>
  );
}
