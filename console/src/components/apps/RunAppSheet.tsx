import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getDeployment, listDevices, listEndpoints, runApp, type AppRun } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getRegistry, listImages, listImageTags } from "@/api/registry";
import { AppAddressPicker } from "@/components/fleet/AppAddressPicker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const POLL_MS = 2000;
const selectClass = "h-10 w-full rounded-md border border-border bg-background px-3 text-sm";

type Port = { host: string; container: string };
type EnvRow = { key: string; value: string; secret: boolean };

type RunAppSheetProps = {
  token: string;
  organizationId: string;
  /** Fixed device; otherwise the user chooses one. */
  deviceId?: string;
  /** Fixed image of the organization's registry, such as web:1.0; otherwise the user chooses one. */
  image?: string;
  canPublish: boolean;
  onClose: () => void;
};

function defaultName(image: string): string {
  return (image.split("@")[0].split("/").pop() ?? "")
    .split(":")[0]
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-");
}

function isPort(value: string): boolean {
  return /^\d{1,5}$/.test(value) && Number(value) >= 1 && Number(value) <= 65535;
}

function ImageField({
  token,
  organizationId,
  value,
  onChange,
}: {
  token: string;
  organizationId: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [repository, setRepository] = useState("");
  const images = useQuery({
    queryKey: ["images", organizationId, "", 1, 100, token],
    queryFn: () => listImages(token, organizationId, { page_size: 100 }),
  });
  const tags = useQuery({
    queryKey: ["image-tags", organizationId, repository, 1, 100, token],
    queryFn: () => listImageTags(token, organizationId, repository, 1, 100),
    enabled: Boolean(repository),
  });
  const newest = tags.data?.items[0]?.tag;
  useEffect(() => {
    // The newest tag, until the user chooses one.
    if (repository && newest && !value.startsWith(`${repository}:`)) {
      onChange(`${repository}:${newest}`);
    }
  }, [repository, newest, value, onChange]);

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <Label htmlFor="run-repository">Image</Label>
        <select
          id="run-repository"
          className={selectClass}
          value={repository}
          onChange={(event) => {
            setRepository(event.target.value);
            onChange("");
          }}
          required
        >
          <option value="">Choose an image</option>
          {(images.data?.items ?? []).map((item) => (
            <option key={item.repository}>{item.repository}</option>
          ))}
        </select>
      </div>
      <div>
        <Label htmlFor="run-tag">Tag</Label>
        <select
          id="run-tag"
          className={selectClass}
          value={value.slice(repository.length + 1)}
          disabled={!repository}
          onChange={(event) => onChange(`${repository}:${event.target.value}`)}
          required
        >
          {(tags.data?.items ?? []).map((item) => (
            <option key={item.tag}>{item.tag}</option>
          ))}
        </select>
      </div>
      {images.isSuccess && images.data.total === 0 && (
        <p className="text-xs text-muted-foreground sm:col-span-2">
          No images yet.{" "}
          <Link className="text-link hover:underline" to={`/organizations/${organizationId}/images`}>
            Push one first
          </Link>
          .
        </p>
      )}
    </div>
  );
}

/** What the Deploy of one device says, in the words of the sheet. */
export function runState(device: { status: string; health: string | null } | undefined): {
  label: string;
  done: boolean;
  failed: boolean;
} {
  const state = (label: string, done = false, failed = false) => ({ label, done, failed });
  switch (device?.status) {
    case "succeeded":
      if (device.health === "healthy") return state("Healthy", true);
      if (device.health === "unhealthy") return state("Failed", true, true);
      return state("Starting");
    case "updating":
      return state("Pulling and starting");
    case "failed":
      return state("Failed", true, true);
    case "skipped":
      return state("Skipped", true, true);
    case "cancelled":
      return state("Cancelled", true, true);
    default:
      return state("Waiting for the device");
  }
}

function RunProgress({
  token,
  organizationId,
  deviceId,
  run,
  published,
  onClose,
}: {
  token: string;
  organizationId: string;
  deviceId: string;
  run: AppRun;
  published: boolean;
  onClose: () => void;
}) {
  const deployment = useQuery({
    queryKey: ["deployment", organizationId, run.deployment.id, token],
    queryFn: () => getDeployment(token, organizationId, run.deployment.id),
    initialData: run.deployment,
    refetchInterval: (query) => (runState(query.state.data?.devices?.[0]).done ? false : POLL_MS),
  });
  const device = deployment.data.devices?.[0];
  const state = runState(device);
  const endpoints = useQuery({
    queryKey: ["endpoints", organizationId, deviceId, token],
    queryFn: () => listEndpoints(token, organizationId, deviceId),
    enabled: published && state.done && !state.failed,
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) => item.workload_key === run.workload_key && item.availability === "ready",
      )
        ? false
        : POLL_MS,
  });
  const endpoint = endpoints.data?.find((item) => item.workload_key === run.workload_key);

  return (
    <div className="space-y-4">
      <p role="status" className="text-lg font-medium">
        {state.label}
        {!state.done && "…"}
      </p>
      {device?.last_error && (
        <p role="alert" className="break-words rounded-md bg-ember px-2 py-1 text-sm text-midnight">
          {device.last_error}
        </p>
      )}
      {published && state.done && !state.failed && endpoint?.availability !== "ready" && (
        <p className="text-sm text-muted-foreground">The public address is being prepared…</p>
      )}
      <div className="flex flex-wrap gap-2">
        {endpoint?.availability === "ready" && (
          <Button asChild>
            <a href={endpoint.public_url} target="_blank" rel="noreferrer">
              Open URL
            </a>
          </Button>
        )}
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/deployments/${run.deployment.id}`}>
            View Deploy
          </Link>
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}

export function RunAppSheet({
  token,
  organizationId,
  deviceId: fixedDevice,
  image: fixedImage,
  canPublish,
  onClose,
}: RunAppSheetProps) {
  const queryClient = useQueryClient();
  const [deviceId, setDeviceId] = useState(fixedDevice ?? "");
  const [source, setSource] = useState<"meteorcloud" | "public">("meteorcloud");
  const [image, setImage] = useState(fixedImage ?? "");
  const [editedName, setEditedName] = useState<string | null>(null);
  const [ports, setPorts] = useState<Port[]>([{ host: "8080", container: "8080" }]);
  const [env, setEnv] = useState<EnvRow[]>([]);
  const [publish, setPublish] = useState(false);
  const [hostname, setHostname] = useState<string | null>(null);
  const [run, setRun] = useState<AppRun | null>(null);

  const registry = useQuery({
    queryKey: ["registry", organizationId, token],
    queryFn: () => getRegistry(token, organizationId),
  });
  const devices = useQuery({
    queryKey: ["devices", organizationId, "run-app", token],
    queryFn: () => listDevices(token, organizationId, { page_size: 100 }),
    enabled: !fixedDevice,
  });
  const ownRegistry = Boolean(registry.data?.enabled);
  const fromRegistry = Boolean(fixedImage) || (ownRegistry && source === "meteorcloud");
  const name = editedName ?? defaultName(image);
  const badPort = ports.some((port) => !isPort(port.host) || !isPort(port.container));
  const badEnv = env.some((row) => row.key && !/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(row.key));
  const ready =
    Boolean(deviceId && image.trim() && name) &&
    registry.isSuccess &&
    !badPort &&
    !badEnv &&
    (!publish || (Boolean(hostname) && ports.length > 0));

  const mutation = useMutation({
    mutationFn: () =>
      runApp(token, organizationId, deviceId, {
        image: image.trim(),
        registry: fromRegistry ? "meteorcloud" : null,
        name,
        ports: ports.map((port) => `${port.host}:${port.container}`),
        env: Object.fromEntries(env.filter((row) => row.key).map((row) => [row.key, row.value])),
        endpoint: publish ? { hostname, port: Number(ports[0].host) } : null,
      }),
    onSuccess: async (result) => {
      setRun(result);
      await queryClient.invalidateQueries({ queryKey: ["device-software", organizationId] });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  function setPort(index: number, change: Partial<Port>) {
    setPorts(ports.map((port, i) => (i === index ? { ...port, ...change } : port)));
  }

  function setEnvRow(index: number, change: Partial<EnvRow>) {
    setEnv(env.map((row, i) => (i === index ? { ...row, ...change } : row)));
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-app-title"
        className="h-full w-full max-w-lg overflow-y-auto border-l border-border bg-card p-6 shadow-lg"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 id="run-app-title" className="text-xl font-semibold">
            Run an app
          </h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>

        {run ? (
          <RunProgress
            token={token}
            organizationId={organizationId}
            deviceId={deviceId}
            run={run}
            published={publish}
            onClose={onClose}
          />
        ) : (
          <form className="space-y-5" onSubmit={onSubmit}>
            {fixedImage ? (
              <p className="break-all font-mono text-sm">{fixedImage}</p>
            ) : (
              <>
                {ownRegistry && (
                  <div>
                    <Label htmlFor="run-source">From</Label>
                    <select
                      id="run-source"
                      className={selectClass}
                      value={source}
                      onChange={(event) => {
                        setSource(event.target.value as typeof source);
                        setImage("");
                      }}
                    >
                      <option value="meteorcloud">This organization&apos;s images</option>
                      <option value="public">A public registry</option>
                    </select>
                  </div>
                )}
                {fromRegistry ? (
                  <ImageField
                    token={token}
                    organizationId={organizationId}
                    value={image}
                    onChange={setImage}
                  />
                ) : (
                  <div>
                    <Label htmlFor="run-image">Image</Label>
                    <Input
                      id="run-image"
                      placeholder="nginx:1.27"
                      value={image}
                      onChange={(event) => setImage(event.target.value)}
                      required
                    />
                  </div>
                )}
              </>
            )}

            {!fixedDevice && (
              <div>
                <Label htmlFor="run-device">Device</Label>
                <select
                  id="run-device"
                  className={selectClass}
                  value={deviceId}
                  onChange={(event) => {
                    setDeviceId(event.target.value);
                    setHostname(null);
                  }}
                  required
                >
                  <option value="">Choose a device</option>
                  {(devices.data?.items ?? []).map((device) => (
                    <option key={device.id} value={device.id}>
                      {device.name} ({device.status})
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <Label htmlFor="run-name">Name</Label>
              <Input
                id="run-name"
                value={name}
                maxLength={63}
                onChange={(event) => setEditedName(event.target.value)}
                required
              />
            </div>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Ports (device → container)</legend>
              {ports.map((port, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    aria-label={`Device port ${index + 1}`}
                    inputMode="numeric"
                    value={port.host}
                    onChange={(event) => setPort(index, { host: event.target.value.trim() })}
                  />
                  →
                  <Input
                    aria-label={`Container port ${index + 1}`}
                    inputMode="numeric"
                    value={port.container}
                    onChange={(event) => setPort(index, { container: event.target.value.trim() })}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove port ${index + 1}`}
                    onClick={() => setPorts(ports.filter((_, i) => i !== index))}
                  >
                    Remove
                  </Button>
                </div>
              ))}
              {badPort && <p className="text-xs text-destructive">Ports are 1 to 65535.</p>}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setPorts([...ports, { host: "", container: "" }])}
              >
                Add port
              </Button>
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Environment</legend>
              {env.map((row, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    aria-label={`Variable ${index + 1} name`}
                    placeholder="NAME"
                    value={row.key}
                    onChange={(event) => setEnvRow(index, { key: event.target.value.trim() })}
                  />
                  <Input
                    aria-label={`Variable ${index + 1} value`}
                    type={row.secret ? "password" : "text"}
                    autoComplete="off"
                    value={row.value}
                    onChange={(event) => setEnvRow(index, { value: event.target.value })}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setEnvRow(index, { secret: !row.secret })}
                  >
                    {row.secret ? "Show" : "Hide"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove variable ${index + 1}`}
                    onClick={() => setEnv(env.filter((_, i) => i !== index))}
                  >
                    ✕
                  </Button>
                </div>
              ))}
              {badEnv && (
                <p className="text-xs text-destructive">
                  Names are letters, digits, &quot;_&quot;, &quot;.&quot; and &quot;-&quot;.
                </p>
              )}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setEnv([...env, { key: "", value: "", secret: true }])}
              >
                Add variable
              </Button>
              {env.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Values are hidden here only: members who can see releases can read them.
                </p>
              )}
            </fieldset>

            {canPublish && (
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={publish}
                    disabled={!deviceId || ports.length === 0}
                    onChange={(event) => setPublish(event.target.checked)}
                  />
                  Public URL
                </label>
                {publish && deviceId && (
                  <AppAddressPicker
                    token={token}
                    organizationId={organizationId}
                    deviceId={deviceId}
                    appName={name || "app"}
                    onChange={setHostname}
                  />
                )}
              </div>
            )}

            {mutation.isError && (
              <p role="alert" className="rounded-md bg-ember px-2 py-1 text-sm text-midnight">
                {mutation.error instanceof ApiError
                  ? mutation.error.message
                  : "Could not run the app."}
              </p>
            )}
            <Button type="submit" disabled={!ready || mutation.isPending}>
              {mutation.isPending ? "Starting…" : "Run"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
