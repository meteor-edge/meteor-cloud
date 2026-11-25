import { ChangeEvent, FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  clearEnrollmentKeyAutoApprove,
  listDeviceGroups,
  listDeviceTypes,
  setEnrollmentKeyAutoApprove,
  type EnrollmentApiKey,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { parseAllowlist } from "@/lib/autoApprove";

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function toLocalInput(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

type AutoApproveRuleDialogProps = {
  token: string;
  organizationId: string;
  enrollmentKey: EnrollmentApiKey;
  onClose: () => void;
  onSaved: () => void;
};

export function AutoApproveRuleDialog({
  token,
  organizationId,
  enrollmentKey,
  onClose,
  onSaved,
}: AutoApproveRuleDialogProps) {
  const rule = enrollmentKey.auto_approve_rule ?? null;
  const [typeId, setTypeId] = useState(rule?.device_type_id ?? "");
  const [groupId, setGroupId] = useState(rule?.device_group_id ?? "");
  const [allowlistText, setAllowlistText] = useState((rule?.allowlist ?? []).join("\n"));
  const [maxDevices, setMaxDevices] = useState(rule?.max_devices ? String(rule.max_devices) : "");
  const [until, setUntil] = useState(toLocalInput(rule?.until));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token, organizationId),
  });
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token, organizationId),
  });

  const allowlist = parseAllowlist(allowlistText);
  const max = maxDevices.trim() ? Number(maxDevices) : null;
  const bounded = allowlist.length > 0 || (max !== null && max >= 1);

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    const added = parseAllowlist(await readText(file));
    setAllowlistText((current) => parseAllowlist(`${current}\n${added.join("\n")}`).join("\n"));
    event.target.value = "";
  }

  async function run(action: () => Promise<unknown>) {
    setSaving(true);
    setError(null);
    try {
      await action();
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the rule.");
    } finally {
      setSaving(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!bounded) {
      setError("Add serials or MACs to the allow-list, set a maximum, or both.");
      return;
    }
    void run(() =>
      setEnrollmentKeyAutoApprove(token, organizationId, enrollmentKey.id, {
        device_type_id: typeId || null,
        device_group_id: groupId || null,
        allowlist,
        max_devices: max,
        until: until ? new Date(until).toISOString() : null,
      }),
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Auto-approve rule"
    >
      <form
        className="grid max-h-full w-full max-w-lg gap-3 overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-glow"
        onSubmit={onSubmit}
      >
        <div>
          <h2 className="text-lg font-semibold">
            Auto-approve devices from “{enrollmentKey.name}”
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Matching devices are approved as soon as they ask, so a flashed batch joins without
            clicking through the queue. Hardware that is already a device always waits for you.
          </p>
        </div>
        <div>
          <Label htmlFor="rule-allowlist">Allow-list (serials or MAC addresses)</Label>
          <textarea
            id="rule-allowlist"
            className="flex min-h-28 w-full rounded-md border border-input bg-field px-3 py-2 font-mono text-xs text-foreground"
            value={allowlistText}
            onChange={(event) => setAllowlistText(event.target.value)}
            placeholder={"SN-0001\ndc:a6:32:00:00:01"}
          />
          <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              {allowlist.length > 0
                ? `${allowlist.length} entr${allowlist.length === 1 ? "y" : "ies"}`
                : "Empty: any device, up to the maximum."}
            </span>
            <label className="cursor-pointer underline">
              Add from CSV
              <input
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                className="sr-only"
                aria-label="Add from CSV"
                onChange={onFile}
              />
            </label>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="rule-max">Maximum devices</Label>
            <Input
              id="rule-max"
              type="number"
              min={1}
              value={maxDevices}
              onChange={(event) => setMaxDevices(event.target.value)}
            />
            {rule && (
              <p className="mt-1 text-xs text-muted-foreground">
                {rule.approved_count} approved so far (counts toward the maximum).
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="rule-until">Ends</Label>
            <Input
              id="rule-until"
              type="datetime-local"
              value={until}
              onChange={(event) => setUntil(event.target.value)}
            />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="rule-type">Device type</Label>
            <select
              id="rule-type"
              className="flex h-10 w-full rounded-md border border-input bg-field px-3 text-sm text-foreground"
              value={typeId}
              onChange={(event) => setTypeId(event.target.value)}
            >
              <option value="">None</option>
              {(typesQuery.data ?? []).map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="rule-group">Device group</Label>
            <select
              id="rule-group"
              className="flex h-10 w-full rounded-md border border-input bg-field px-3 text-sm text-foreground"
              value={groupId}
              onChange={(event) => setGroupId(event.target.value)}
            >
              <option value="">None</option>
              {(groupsQuery.data ?? []).map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          {rule && (
            <Button
              type="button"
              variant="ghost"
              disabled={saving}
              onClick={() =>
                void run(() =>
                  clearEnrollmentKeyAutoApprove(token, organizationId, enrollmentKey.id),
                )
              }
            >
              Turn off
            </Button>
          )}
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>
            Save rule
          </Button>
        </div>
      </form>
    </div>
  );
}
