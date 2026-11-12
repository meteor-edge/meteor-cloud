import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import {
  addDeviceUser,
  listDeviceActions,
  rebootDevice,
  setDevicePassword,
  shutdownDevice,
  type DeviceAction,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { PasswordWithGenerate } from "@/components/PasswordWithGenerate";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn, formatDateTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
  canPower: boolean;
  canUsers: boolean;
};

const KIND_LABEL: Record<DeviceAction["kind"], string> = {
  reboot: "Restart",
  shutdown: "Shut down",
  set_password: "Change password",
  add_user: "Add user",
};

function note(action: DeviceAction): { text: string; tone: string } {
  if (action.state === "pending") {
    return { text: "Waiting for the device (its next heartbeat).", tone: "text-muted-foreground" };
  }
  if (action.state === "sent") {
    return { text: "Collected by the device.", tone: "text-muted-foreground" };
  }
  if (action.state === "failed") {
    return {
      text: action.last_error ?? "The device could not complete this action.",
      tone: "text-destructive",
    };
  }
  const when = action.reported_at ? ` ${formatDateTime(action.reported_at)}` : "";
  if (action.kind === "set_password" || action.kind === "add_user") {
    return {
      text: `Done for ${action.username ?? "user"}${when}.`,
      tone: "text-muted-foreground",
    };
  }
  return { text: `Done${when}.`, tone: "text-muted-foreground" };
}

function isInFlight(action: DeviceAction): boolean {
  return action.state === "pending" || action.state === "sent";
}

/** Restart, shut down, and SSH user changes ordered from the console. */
export function DeviceActionsPanel({ token, organizationId, deviceId, canPower, canUsers }: Props) {
  const queryClient = useQueryClient();
  const queryKey = ["device-actions", organizationId, deviceId];
  const deviceKey = ["device", organizationId, deviceId];
  const [userForm, setUserForm] = useState<"password" | "add" | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sudo, setSudo] = useState(false);
  const [authorizedKey, setAuthorizedKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  // After an order, keep polling even if the first cache write races; clear once settled.
  const [watchUntilSettled, setWatchUntilSettled] = useState(false);

  const actionsQuery = useQuery({
    queryKey,
    queryFn: () => listDeviceActions(token, organizationId, deviceId),
    refetchInterval: (query) => {
      const rows = query.state.data?.actions ?? [];
      if (watchUntilSettled || rows.some(isInFlight)) {
        return 5000;
      }
      return 30000;
    },
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    const rows = actionsQuery.data?.actions ?? [];
    if (watchUntilSettled && rows.length > 0 && !rows.some(isInFlight)) {
      setWatchUntilSettled(false);
      void queryClient.invalidateQueries({ queryKey: ["device", organizationId, deviceId] });
    }
  }, [actionsQuery.data, watchUntilSettled, queryClient, organizationId, deviceId]);

  const onError = (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : "The action could not be saved.");
  const onDone = (data: { actions: DeviceAction[] }) => {
    queryClient.setQueryData(queryKey, data);
    setWatchUntilSettled(true);
    setUserForm(null);
    setPassword("");
    setAuthorizedKey("");
    setError(null);
    void queryClient.invalidateQueries({ queryKey: deviceKey });
  };

  const rebootMutation = useMutation({
    mutationFn: () => rebootDevice(token, organizationId, deviceId),
    onSuccess: onDone,
    onError,
  });
  const shutdownMutation = useMutation({
    mutationFn: () => shutdownDevice(token, organizationId, deviceId),
    onSuccess: onDone,
    onError,
  });
  const passwordMutation = useMutation({
    mutationFn: () => setDevicePassword(token, organizationId, deviceId, { username, password }),
    onSuccess: onDone,
    onError,
  });
  const addUserMutation = useMutation({
    mutationFn: () =>
      addDeviceUser(token, organizationId, deviceId, {
        username,
        password,
        sudo,
        ...(authorizedKey.trim() ? { authorized_key: authorizedKey.trim() } : {}),
      }),
    onSuccess: onDone,
    onError,
  });

  const startForm = (kind: "password" | "add") => {
    setUserForm(kind);
    setUsername("");
    setPassword("");
    setSudo(false);
    setAuthorizedKey("");
    setError(null);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (password.length < 8 || password.length > 128) {
      setError("The password must be 8 to 128 characters.");
      return;
    }
    if (userForm === "password") passwordMutation.mutate();
    else if (userForm === "add") addUserMutation.mutate();
  };

  const refresh = async () => {
    await Promise.all([
      actionsQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: deviceKey }),
    ]);
  };

  const actions = actionsQuery.data?.actions ?? [];
  const busy =
    rebootMutation.isPending ||
    shutdownMutation.isPending ||
    passwordMutation.isPending ||
    addUserMutation.isPending;
  const refreshing = actionsQuery.isFetching && !busy;

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Power and SSH users</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Restart or shut the device down, change a user&apos;s password, or add an SSH user
            (owners and admins). The device picks the action up on its next heartbeat. Passwords are
            sent encrypted and never shown again. This list refreshes while an action is waiting;
            use Refresh anytime.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          className="h-10 w-10 shrink-0 rounded-full p-0"
          aria-label="Refresh actions"
          title="Refresh"
          disabled={refreshing}
          onClick={() => void refresh()}
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
        </Button>
      </div>

      {canPower && (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Restart this device? It will be offline for a short time.")) {
                rebootMutation.mutate();
              }
            }}
          >
            Restart
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  "Shut this device down? Someone will need to power it on again at the site.",
                )
              ) {
                shutdownMutation.mutate();
              }
            }}
          >
            Shut down
          </Button>
        </div>
      )}

      {canUsers && (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy} onClick={() => startForm("password")}>
            Change SSH password
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => startForm("add")}>
            Add SSH user
          </Button>
        </div>
      )}

      {userForm && (
        <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-border p-4">
          <h3 className="text-sm font-semibold">
            {userForm === "password" ? "Change SSH password" : "Add SSH user"}
          </h3>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="action-username">Username</Label>
              <Input
                id="action-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="pi"
                autoCapitalize="off"
                spellCheck={false}
                required
              />
            </div>
            <div>
              <Label htmlFor="action-password">Password</Label>
              <PasswordWithGenerate
                id="action-password"
                value={password}
                onChange={setPassword}
                length={16}
              />
            </div>
          </div>
          {userForm === "add" && (
            <>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={sudo}
                  onChange={(event) => setSudo(event.target.checked)}
                />
                Allow this user to run sudo without a password
              </label>
              <div>
                <Label htmlFor="action-key">SSH public key (optional)</Label>
                <Input
                  id="action-key"
                  value={authorizedKey}
                  onChange={(event) => setAuthorizedKey(event.target.value)}
                  placeholder="ssh-ed25519 AAAA… you@laptop"
                  spellCheck={false}
                />
              </div>
            </>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Sending…" : "Send to device"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setUserForm(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      {actionsQuery.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {!actionsQuery.isLoading && actions.length === 0 && (
        <p className="text-sm text-muted-foreground">No recent actions yet.</p>
      )}
      {actions.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border">
          {actions.map((action) => {
            const status = note(action);
            return (
              <li key={action.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {KIND_LABEL[action.kind]}
                    {action.username ? ` · ${action.username}` : ""}
                    {action.sudo ? " · sudo" : ""}
                  </p>
                  <p className={`mt-1 text-xs ${status.tone}`}>{status.text}</p>
                </div>
                <p className="text-xs text-muted-foreground">{formatDateTime(action.created_at)}</p>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
