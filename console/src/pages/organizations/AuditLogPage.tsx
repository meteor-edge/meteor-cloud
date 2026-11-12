import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { listAuditEvents, type AuditEvent } from "@/api/audit";
import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const PAGE_SIZE = 50;

const OUTCOMES = ["", "success", "denied", "failure", "partial"];

type Cursor = { before: string; before_id: string };

function actorLabel(event: AuditEvent): string {
  if (event.actor_email) return event.actor_email;
  if (!event.actor_type) return "—";
  if (!event.actor_id) return event.actor_type;
  return `${event.actor_type} ${event.actor_id.slice(0, 8)}`;
}

function outcomeClass(outcome: string): string {
  if (outcome === "success") return "text-muted-foreground";
  return "font-semibold text-destructive";
}

export function AuditLogPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const [action, setAction] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [outcome, setOutcome] = useState("");
  const [cursors, setCursors] = useState<Cursor[]>([]);
  const cursor = cursors[cursors.length - 1];

  const filters = {
    action: action.trim() || undefined,
    resource_id: resourceId.trim() || undefined,
    outcome: outcome || undefined,
    limit: PAGE_SIZE,
    ...(cursor ?? {}),
  };

  const query = useQuery({
    queryKey: ["audit-events", organizationId, token, filters],
    queryFn: () => listAuditEvents(token!, organizationId, filters),
    enabled: Boolean(token && organizationId),
  });

  const events = query.data ?? [];
  const last = events[events.length - 1];

  function resetPaging() {
    setCursors([]);
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Audit log</h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Who did what in this organization: sign-ins, member and role changes, keys and tokens,
          device enrollment, deploys, remote access, and refused requests. Rows cannot be edited.
        </p>
      </div>

      <form
        className="grid gap-3 rounded-lg border border-border bg-card p-5 shadow-glow md:grid-cols-3"
        onSubmit={(event) => event.preventDefault()}
      >
        <div>
          <Label htmlFor="audit-action">Action</Label>
          <Input
            id="audit-action"
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              resetPaging();
            }}
            placeholder="e.g. device. or auth.login"
            maxLength={80}
          />
        </div>
        <div>
          <Label htmlFor="audit-resource">Resource ID</Label>
          <Input
            id="audit-resource"
            value={resourceId}
            onChange={(event) => {
              setResourceId(event.target.value);
              resetPaging();
            }}
            placeholder="Device, key or member ID"
            maxLength={64}
          />
        </div>
        <div>
          <Label htmlFor="audit-outcome">Outcome</Label>
          <select
            id="audit-outcome"
            className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={outcome}
            onChange={(event) => {
              setOutcome(event.target.value);
              resetPaging();
            }}
          >
            {OUTCOMES.map((value) => (
              <option key={value} value={value}>
                {value || "Any"}
              </option>
            ))}
          </select>
        </div>
      </form>

      {query.isError && (
        <p className="text-sm text-destructive" role="alert">
          {query.error instanceof ApiError ? query.error.message : "Could not load the audit log."}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-semibold">When</th>
              <th className="px-4 py-3 font-semibold">Actor</th>
              <th className="px-4 py-3 font-semibold">Action</th>
              <th className="px-4 py-3 font-semibold">Resource</th>
              <th className="px-4 py-3 font-semibold">Outcome</th>
              <th className="px-4 py-3 font-semibold">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border last:border-0 align-top">
                <td className="whitespace-nowrap px-4 py-3" title={event.created_at}>
                  {new Date(event.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-3">
                  <div>{actorLabel(event)}</div>
                  {event.ip && <div className="text-xs text-muted-foreground">{event.ip}</div>}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{event.action}</td>
                <td className="px-4 py-3">
                  <div>{event.resource_type}</div>
                  {event.resource_id && (
                    <div className="font-mono text-xs text-muted-foreground">{event.resource_id}</div>
                  )}
                </td>
                <td className={`px-4 py-3 ${outcomeClass(event.outcome)}`}>{event.outcome}</td>
                <td className="max-w-xs px-4 py-3">
                  {Object.keys(event.metadata).length > 0 && (
                    <code className="block break-all text-xs text-muted-foreground">
                      {JSON.stringify(event.metadata)}
                    </code>
                  )}
                </td>
              </tr>
            ))}
            {!query.isLoading && events.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">
                  No audit events match.
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
          disabled={!last || events.length < PAGE_SIZE}
          onClick={() =>
            last && setCursors((current) => [...current, { before: last.created_at, before_id: last.id }])
          }
        >
          Older
        </Button>
      </div>
    </section>
  );
}
