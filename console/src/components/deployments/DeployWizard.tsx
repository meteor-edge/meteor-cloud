import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  createDeployment,
  listDeviceGroups,
  listDevices,
  listReleases,
  type DeploymentCreatePayload,
  type Release,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { parseYamlObject } from "@/lib/yaml";

const KIND_LABELS: Record<string, string> = {
  systemd: "systemd service",
  docker: "Single container",
  docker_compose: "Compose application",
};

const KIND_CAPABILITY: Record<string, string> = {
  systemd: "systemd",
  docker: "docker",
  docker_compose: "docker_compose",
};

type DeployWizardProps = {
  token: string;
  organizationId: string;
  initialReleaseId?: string | null;
  initialDeviceId?: string | null;
  onClose: () => void;
  onCreated: (deploymentId: string) => void;
};

export function DeployWizard({
  token,
  organizationId,
  initialReleaseId = null,
  initialDeviceId = null,
  onClose,
  onCreated,
}: DeployWizardProps) {
  const [mode, setMode] = useState<"wizard" | "yaml">("wizard");
  const [step, setStep] = useState(1);
  const [releaseId, setReleaseId] = useState(initialReleaseId ?? "");
  const [deviceIds, setDeviceIds] = useState<string[]>(
    initialDeviceId ? [initialDeviceId] : [],
  );
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [batchSize, setBatchSize] = useState("");
  const [yamlText, setYamlText] = useState(
    [
      "release_id: ",
      "name: null",
      "target:",
      "  device_ids: []",
      "  group_ids: []",
      "rollout:",
      "  strategy: immediate",
    ].join("\n"),
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const releasesQuery = useQuery({
    queryKey: ["releases", organizationId, "deploy-wizard", token],
    queryFn: () => listReleases(token, organizationId, { page_size: 100 }),
  });
  const devicesQuery = useQuery({
    queryKey: ["devices", organizationId, "deploy-wizard", token],
    queryFn: () => listDevices(token, organizationId, { page_size: 100, enabled: true }),
  });
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, "deploy-wizard", token],
    queryFn: () => listDeviceGroups(token, organizationId),
  });

  const releases = releasesQuery.data?.items ?? [];
  const selectedRelease: Release | undefined = useMemo(
    () => releases.find((item) => item.id === releaseId),
    [releases, releaseId],
  );
  const devices = devicesQuery.data?.items ?? [];
  const groups = groupsQuery.data ?? [];

  const skippedDevices = useMemo(() => {
    const capKey = selectedRelease ? KIND_CAPABILITY[selectedRelease.kind] : undefined;
    if (!capKey || deviceIds.length === 0) {
      return [];
    }
    return devices.filter((device) => {
      if (!deviceIds.includes(device.id)) {
        return false;
      }
      const caps = device.metadata?.capabilities;
      if (!caps || typeof caps !== "object" || Array.isArray(caps)) {
        return false;
      }
      const record = caps as Record<string, unknown>;
      if (!(capKey in record)) {
        return false;
      }
      return record[capKey] === false;
    });
  }, [selectedRelease, deviceIds, devices]);

  function toggleId(list: string[], id: string, setList: (next: string[]) => void) {
    setList(list.includes(id) ? list.filter((value) => value !== id) : [...list, id]);
  }

  function buildPayload(): DeploymentCreatePayload {
    const size = batchSize.trim() ? Number(batchSize) : null;
    const rollout =
      size && size > 0
        ? { strategy: "batched", batch_size: size }
        : { strategy: "immediate" };
    return {
      release_id: releaseId,
      name: name.trim() || null,
      target: {
        device_ids: deviceIds,
        group_ids: groupIds,
      },
      rollout,
    };
  }

  async function submitPayload(payload: DeploymentCreatePayload) {
    setSubmitting(true);
    setError(null);
    try {
      const deployment = await createDeployment(token, organizationId, payload);
      onCreated(deployment.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the deployment.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onDeploy() {
    if (!releaseId) {
      setError("Select a release.");
      return;
    }
    if (deviceIds.length === 0 && groupIds.length === 0) {
      setError("Select at least one device or group.");
      return;
    }
    await submitPayload(buildPayload());
  }

  async function onDeployYaml() {
    try {
      const raw = parseYamlObject(yamlText);
      const target = (raw.target as Record<string, unknown> | undefined) ?? {};
      const rolloutRaw = (raw.rollout as Record<string, unknown> | undefined) ?? {};
      const payload: DeploymentCreatePayload = {
        release_id: String(raw.release_id ?? ""),
        name: raw.name == null ? null : String(raw.name),
        target: {
          device_ids: Array.isArray(target.device_ids)
            ? target.device_ids.map(String)
            : [],
          group_ids: Array.isArray(target.group_ids) ? target.group_ids.map(String) : [],
          device_type_ids: Array.isArray(target.device_type_ids)
            ? target.device_type_ids.map(String)
            : [],
        },
        rollout: {
          strategy: String(rolloutRaw.strategy ?? "immediate"),
          batch_size:
            rolloutRaw.batch_size == null ? null : Number(rolloutRaw.batch_size),
          delay_seconds:
            rolloutRaw.delay_seconds == null ? null : Number(rolloutRaw.delay_seconds),
        },
      };
      if (!payload.release_id) {
        setError("YAML must include release_id.");
        return;
      }
      await submitPayload(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid YAML.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="deploy-wizard-title"
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="deploy-wizard-title" className="text-xl font-semibold">
              Deploy release
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Choose a release and targets. Devices pick up desired software on the next
              heartbeat.
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>

        <div className="mt-4 flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={mode === "wizard" ? "default" : "outline"}
            onClick={() => setMode("wizard")}
          >
            Wizard
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === "yaml" ? "default" : "outline"}
            onClick={() => setMode("yaml")}
          >
            Advanced YAML
          </Button>
        </div>

        {mode === "yaml" ? (
          <div className="mt-6 space-y-3">
            <label className="block text-sm font-medium" htmlFor="deploy-yaml">
              Deploy body (YAML)
            </label>
            <textarea
              id="deploy-yaml"
              className="min-h-[220px] w-full rounded-md border border-input bg-field px-3 py-2 font-mono text-sm"
              value={yamlText}
              onChange={(event) => setYamlText(event.target.value)}
              spellCheck={false}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" disabled={submitting} onClick={() => void onDeployYaml()}>
                {submitting ? "Deploying…" : "Deploy"}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="mt-4 flex gap-2 text-sm text-muted-foreground">
              <span className={step === 1 ? "font-medium text-foreground" : ""}>1. Release</span>
              <span aria-hidden>→</span>
              <span className={step === 2 ? "font-medium text-foreground" : ""}>2. Targets</span>
              <span aria-hidden>→</span>
              <span className={step === 3 ? "font-medium text-foreground" : ""}>3. Review</span>
            </div>

            {step === 1 && (
              <div className="mt-6 space-y-3">
                <label className="block text-sm font-medium" htmlFor="deploy-release">
                  Release
                </label>
                <select
                  id="deploy-release"
                  className="h-10 w-full rounded-md border border-input bg-field px-3 text-sm"
                  value={releaseId}
                  onChange={(event) => setReleaseId(event.target.value)}
                >
                  <option value="">Select a release…</option>
                  {releases.map((release) => (
                    <option key={release.id} value={release.id}>
                      {release.name} {release.version} (
                      {KIND_LABELS[release.kind] ?? release.kind})
                    </option>
                  ))}
                </select>
                <Input
                  placeholder="Optional name for this deploy"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" disabled={!releaseId} onClick={() => setStep(2)}>
                    Next
                  </Button>
                </div>
              </div>
            )}

            {step === 2 && (
              <div className="mt-6 space-y-6">
                <div>
                  <p className="text-sm font-medium">Device groups</p>
                  <div className="mt-2 max-h-40 space-y-2 overflow-y-auto rounded-md border border-border p-3">
                    {groups.length === 0 && (
                      <p className="text-sm text-muted-foreground">No groups yet.</p>
                    )}
                    {groups.map((group) => (
                      <label key={group.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={groupIds.includes(group.id)}
                          onChange={() => toggleId(groupIds, group.id, setGroupIds)}
                        />
                        {group.name}
                        <span className="text-muted-foreground">({group.device_count})</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-sm font-medium">Devices</p>
                  <div className="mt-2 max-h-48 space-y-2 overflow-y-auto rounded-md border border-border p-3">
                    {devices.length === 0 && (
                      <p className="text-sm text-muted-foreground">No enabled devices.</p>
                    )}
                    {devices.map((device) => (
                      <label key={device.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={deviceIds.includes(device.id)}
                          onChange={() => toggleId(deviceIds, device.id, setDeviceIds)}
                        />
                        {device.name}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="flex justify-between gap-2 pt-2">
                  <Button type="button" variant="outline" onClick={() => setStep(1)}>
                    Back
                  </Button>
                  <Button
                    type="button"
                    disabled={deviceIds.length === 0 && groupIds.length === 0}
                    onClick={() => setStep(3)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}

            {step === 3 && (
              <div className="mt-6 space-y-4">
                <dl className="space-y-2 text-sm">
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Release</dt>
                    <dd className="font-medium">
                      {selectedRelease
                        ? `${selectedRelease.name} ${selectedRelease.version}`
                        : releaseId}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Groups</dt>
                    <dd>{groupIds.length}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Devices</dt>
                    <dd>{deviceIds.length}</dd>
                  </div>
                </dl>
                <div className="space-y-2">
                  <label className="block text-sm font-medium" htmlFor="deploy-batch">
                    Batch size (optional)
                  </label>
                  <Input
                    id="deploy-batch"
                    type="number"
                    min={1}
                    placeholder="All at once"
                    value={batchSize}
                    onChange={(event) => setBatchSize(event.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Leave empty to deploy to every target immediately. Set a number to
                    activate that many devices at a time.
                  </p>
                </div>
                {skippedDevices.length > 0 && (
                  <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                    {skippedDevices.length} selected device
                    {skippedDevices.length === 1 ? "" : "s"} reported no{" "}
                    {selectedRelease ? KIND_CAPABILITY[selectedRelease.kind] : "runtime"}{" "}
                    support and will be skipped:{" "}
                    {skippedDevices.map((d) => d.name).join(", ")}.
                  </p>
                )}
                {error && <p className="text-sm text-destructive">{error}</p>}
                <div className="flex justify-between gap-2 pt-2">
                  <Button type="button" variant="outline" onClick={() => setStep(2)}>
                    Back
                  </Button>
                  <Button type="button" disabled={submitting} onClick={() => void onDeploy()}>
                    {submitting ? "Deploying…" : "Deploy"}
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
