import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import {
  createIntegration,
  deleteIntegration,
  getChannelCapabilities,
  listIntegrations,
  testIntegration,
  updateIntegration,
  type Integration,
  type IntegrationBody,
  type IntegrationKind,
} from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canEditRules } from "@/lib/permissions";

import { KIND_LABEL } from "./channelKinds";
import { cardClass, errorText, selectClass, useOrgRole } from "./shared";

function describe(item: Integration): string {
  if (item.kind === "email") return item.recipients.join(", ");
  if (item.kind === "telegram") return `chat ${item.chat_id}`;
  return item.url + (item.has_secret ? " · signed" : "");
}

export function ChannelsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = canEditRules(useOrgRole(organizationId));
  const [editing, setEditing] = useState<Integration | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({});
  const enabled = Boolean(token && organizationId);
  const integrations = useQuery({
    queryKey: ["notification-integrations", organizationId, token],
    queryFn: () => listIntegrations(token!, organizationId),
    enabled,
  });
  const capabilities = useQuery({
    queryKey: ["channel-capabilities", organizationId, token],
    queryFn: () => getChannelCapabilities(token!, organizationId),
    enabled,
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["notification-integrations", organizationId] });
  const toggle = useMutation({
    mutationFn: (item: Integration) =>
      updateIntegration(token!, organizationId, item.id, { enabled: !item.enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteIntegration(token!, organizationId, id),
    onSuccess: refresh,
  });
  const test = useMutation({
    mutationFn: (id: string) => testIntegration(token!, organizationId, id),
    onMutate: (id) => setResults((all) => ({ ...all, [id]: { ok: true, text: "Sending…" } })),
    onSuccess: (result, id) =>
      setResults((all) => ({
        ...all,
        [id]: {
          ok: result.delivered,
          text: result.delivered ? "Test message sent." : `Not delivered: ${result.error ?? ""}`,
        },
      })),
    onError: (error, id) =>
      setResults((all) => ({
        ...all,
        [id]: { ok: false, text: errorText(error, "The test could not run.") },
      })),
  });
  const error = integrations.error ?? toggle.error ?? remove.error;

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Notifications</h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Where rules send their alarms: email, a Telegram bot, Slack or your own webhook. Choose
          channels in each rule.
        </p>
      </div>
      {capabilities.data && !capabilities.data.engine && (
        <p className="text-sm text-amber-600" role="status">
          The rule engine is not reachable, so nothing is delivered right now.
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(error, "Could not change the channels.")}
        </p>
      )}
      {integrations.data && (
        <ul className={`divide-y divide-border text-sm ${cardClass}`}>
          {(integrations.data ?? []).map((item) => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div className="min-w-0">
                <span className="font-medium">{item.name}</span>{" "}
                <span className="rounded-full border border-border px-2 py-0.5 text-xs">
                  {KIND_LABEL[item.kind]}
                </span>
                {!item.enabled && <span className="ml-2 text-xs text-muted-foreground">off</span>}
                <div className="break-all text-muted-foreground">{describe(item)}</div>
                {results[item.id] && (
                  <div
                    role="status"
                    className={results[item.id].ok ? "text-emerald-600" : "text-destructive"}
                  >
                    {results[item.id].text}
                  </div>
                )}
              </div>
              {canEdit && (
                <span className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={test.isPending}
                    aria-label={`Send test to ${item.name}`}
                    onClick={() => test.mutate(item.id)}
                  >
                    Send test
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(item)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => toggle.mutate(item)}>
                    {item.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Delete ${item.name}? A channel that a rule uses cannot be deleted.`,
                        )
                      )
                        remove.mutate(item.id);
                    }}
                  >
                    Delete
                  </Button>
                </span>
              )}
            </li>
          ))}
          {integrations.data?.length === 0 && (
            <li className="py-2 text-muted-foreground">No channels yet.</li>
          )}
        </ul>
      )}
      {canEdit && (
        <ChannelForm
          key={editing?.id ?? "new"}
          organizationId={organizationId}
          token={token}
          editing={editing}
          emailReady={capabilities.data ? capabilities.data.email : true}
          onDone={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
    </section>
  );
}

function ChannelForm({
  organizationId,
  token,
  editing,
  emailReady,
  onDone,
}: {
  organizationId: string;
  token: string | null;
  editing: Integration | null;
  emailReady: boolean;
  onDone: () => void;
}) {
  const [kind, setKind] = useState<IntegrationKind>(editing?.kind ?? "email");
  const [name, setName] = useState(editing?.name ?? "");
  const [url, setUrl] = useState(editing?.url ?? "");
  const [recipients, setRecipients] = useState((editing?.recipients ?? []).join(", "));
  const [chatId, setChatId] = useState(editing?.chat_id ?? "");
  const [secret, setSecret] = useState("");
  const save = useMutation({
    mutationFn: () => {
      const body: IntegrationBody & { name: string } = { name: name.trim() };
      if (kind === "email") body.recipients = recipients.split(/[\s,;]+/).filter(Boolean);
      else if (kind === "telegram") body.chat_id = chatId.trim();
      else body.url = url.trim();
      if (kind !== "email" && secret.trim()) body.secret = secret.trim();
      return editing
        ? updateIntegration(token!, organizationId, editing.id, body)
        : createIntegration(token!, organizationId, { ...body, kind });
    },
    onSuccess: () => {
      setName("");
      setUrl("");
      setRecipients("");
      setChatId("");
      setSecret("");
      onDone();
    },
  });
  return (
    <form
      className={`space-y-4 ${cardClass}`}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h2 className="text-lg font-semibold">
        {editing ? `Edit ${editing.name}` : "Add a channel"}
      </h2>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor="channel-kind">Kind</Label>
          <select
            id="channel-kind"
            className={selectClass}
            value={kind}
            disabled={Boolean(editing)}
            onChange={(e) => {
              setKind(e.target.value as IntegrationKind);
              setSecret("");
            }}
          >
            {(Object.keys(KIND_LABEL) as IntegrationKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="channel-name">Name</Label>
          <Input
            id="channel-name"
            className="mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={120}
          />
        </div>
        {kind === "email" && (
          <div className="md:col-span-2">
            <Label htmlFor="channel-recipients">Recipients</Label>
            <Input
              id="channel-recipients"
              className="mt-1"
              value={recipients}
              onChange={(e) => setRecipients(e.target.value)}
              placeholder="ops@example.com, oncall@example.com"
              required
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Up to 20 addresses. They all see each other.
            </p>
            {!emailReady && (
              <p className="mt-1 text-sm text-amber-600" role="status">
                This platform has no mail server yet. An administrator sets RULES_SMTP_HOST on the
                rule processor (docs/rules.md).
              </p>
            )}
          </div>
        )}
        {kind === "telegram" && (
          <>
            <div>
              <Label htmlFor="channel-token">Bot token</Label>
              <Input
                id="channel-token"
                className="mt-1"
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={editing ? "Keep the current token" : "123456789:AA…"}
                autoComplete="new-password"
                required={!editing}
              />
            </div>
            <div>
              <Label htmlFor="channel-chat">Chat ID</Label>
              <Input
                id="channel-chat"
                className="mt-1"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                placeholder="-1001234567890 or @channelname"
                required
              />
            </div>
            <p className="text-xs text-muted-foreground md:col-span-2">
              Create a bot with @BotFather in Telegram and copy its token. Send the bot a message,
              or add it to your group, then open
              https://api.telegram.org/bot&lt;token&gt;/getUpdates to read the chat ID. The token is
              stored encrypted and never shown again.
            </p>
          </>
        )}
        {(kind === "slack" || kind === "webhook") && (
          <>
            <div>
              <Label htmlFor="channel-url">URL</Label>
              <Input
                id="channel-url"
                className="mt-1"
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>
            <div>
              <Label htmlFor="channel-secret">Signing secret</Label>
              <Input
                id="channel-secret"
                className="mt-1"
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={
                  editing?.has_secret ? "Keep the current secret" : "Optional, 16+ characters"
                }
                autoComplete="new-password"
              />
            </div>
          </>
        )}
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {editing ? "Save channel" : "Add channel"}
        </Button>
        {editing && (
          <Button type="button" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        )}
      </div>
      {save.isError && (
        <p className="text-sm text-destructive" role="alert">
          {errorText(save.error, "Could not save the channel.")}
        </p>
      )}
    </form>
  );
}
