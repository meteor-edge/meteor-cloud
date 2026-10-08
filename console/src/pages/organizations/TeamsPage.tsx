import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { createTeam, listTeams } from "@/api/teams";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageTeams } from "@/lib/permissions";

export function TeamsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const teamsQuery = useQuery({
    queryKey: ["teams", organizationId, token],
    queryFn: () => listTeams(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageTeams(orgQuery.data?.current_user_role);

  const createMutation = useMutation({
    mutationFn: () =>
      createTeam(token!, organizationId, { name, description: description || undefined }),
    onSuccess: async () => {
      setName("");
      setDescription("");
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["teams", organizationId] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create team.");
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  const teams = teamsQuery.data ?? [];

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Teams</h1>
        <p className="mt-2 max-w-xl text-muted-foreground">
          Group members by how they work together, e.g. Berlin on-call or Firmware. Teams do not
          change what anyone can do; roles and device groups on each member still decide that.
        </p>
      </div>

      {canManage && (
        <form
          className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow md:grid-cols-[1fr_1fr_auto]"
          onSubmit={onCreate}
        >
          <div>
            <Label htmlFor="team-name">Name</Label>
            <Input
              id="team-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Berlin on-call"
              maxLength={120}
              required
            />
          </div>
          <div>
            <Label htmlFor="team-description">Description</Label>
            <Input
              id="team-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={createMutation.isPending}>
              Add team
            </Button>
          </div>
        </form>
      )}

      {(error || teamsQuery.error) && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">
          {error ??
            (teamsQuery.error instanceof ApiError
              ? teamsQuery.error.message
              : "Could not load teams.")}
        </p>
      )}

      {teamsQuery.isLoading ? (
        <p className="text-muted-foreground">Loading teams…</p>
      ) : teamsQuery.error ? null : teams.length === 0 ? (
        <p className="text-muted-foreground">No teams yet.</p>
      ) : (
        <ul className="space-y-3">
          {teams.map((team) => (
            <li key={team.id}>
              <Link
                to={`/organizations/${organizationId}/teams/${team.id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4 shadow-glow transition hover:border-link"
              >
                <div>
                  <p className="font-medium">{team.name}</p>
                  {team.description && (
                    <p className="text-sm text-muted-foreground">{team.description}</p>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground">{team.member_count}</span>{" "}
                  {team.member_count === 1 ? "member" : "members"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
