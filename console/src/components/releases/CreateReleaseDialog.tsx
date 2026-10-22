import { FormEvent, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  createRelease,
  listArtifacts,
  type Artifact,
  type ArtifactType,
  type Release,
  type ReleaseCreatePayload,
  type ReleaseKind,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { parseYamlObject } from "@/lib/yaml";

const SELECT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-field text-foreground px-3 text-sm";

/** Kinds offered in the create form today. More (e.g. AI models) can be added later. */
const RELEASE_KINDS: { id: ReleaseKind; label: string; hint: string }[] = [
  {
    id: "systemd",
    label: "systemd service",
    hint: "A Linux service installed and managed with systemctl",
  },
  {
    id: "docker",
    label: "Single container",
    hint: "One container via docker run (pull an image or load a tarball)",
  },
  {
    id: "docker_compose",
    label: "Compose application",
    hint: "An app defined by Docker Compose (one or more services)",
  },
];

const ARTIFACT_TYPE_FOR_KIND: Record<ReleaseKind, ArtifactType | null> = {
  systemd: "systemd",
  docker: "docker",
  docker_compose: "docker_compose",
};

type CreateReleaseDialogProps = {
  token: string;
  organizationId: string;
  onClose: () => void;
  onCreated: (release: Release) => void;
};

export function CreateReleaseDialog({
  token,
  organizationId,
  onClose,
  onCreated,
}: CreateReleaseDialogProps) {
  const [mode, setMode] = useState<"form" | "yaml">("form");
  const [kind, setKind] = useState<ReleaseKind>("systemd");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [artifactId, setArtifactId] = useState("");
  const [unitName, setUnitName] = useState("");
  const [containerName, setContainerName] = useState("");
  const [image, setImage] = useState("");
  const [projectName, setProjectName] = useState("");
  const [description, setDescription] = useState("");
  const [yamlText, setYamlText] = useState(
    [
      "name: camera-agent",
      "version: 1.2.0",
      "kind: systemd",
      "artifact_id: null",
      "description: null",
      "spec:",
      "  unit_name: camera-agent.service",
      "  start: true",
    ].join("\n"),
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const artifactType = ARTIFACT_TYPE_FOR_KIND[kind];
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", organizationId, artifactType, token],
    queryFn: () =>
      listArtifacts(token, organizationId, {
        type: artifactType ?? undefined,
        page_size: 100,
      }),
    enabled: Boolean(token && organizationId && artifactType),
  });

  const artifacts: Artifact[] = artifactsQuery.data?.items ?? [];
  const artifactOptions = useMemo(
    () =>
      artifacts.map((item) => ({
        id: item.id,
        label: `${item.name} ${item.version} (${item.file_name})`,
      })),
    [artifacts],
  );

  useEffect(() => {
    setArtifactId("");
    setError(null);
  }, [kind]);

  const kindMeta = RELEASE_KINDS.find((item) => item.id === kind)!;
  const artifactRequired = kind !== "docker";
  const canSubmit =
    !saving &&
    name.trim() &&
    version.trim() &&
    (kind !== "docker" || Boolean(artifactId) || Boolean(image.trim())) &&
    (kind !== "systemd" || (Boolean(artifactId) && Boolean(unitName.trim()))) &&
    (kind !== "docker_compose" || (Boolean(artifactId) && Boolean(projectName.trim()))) &&
    (kind !== "docker" || Boolean(containerName.trim()));

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (kind === "systemd") {
      if (!artifactId) {
        setError("Choose an artifact for this release.");
        return;
      }
      if (!unitName.trim()) {
        setError("Unit name is required.");
        return;
      }
    }
    if (kind === "docker") {
      if (!containerName.trim()) {
        setError("Container name is required.");
        return;
      }
      if (!artifactId && !image.trim()) {
        setError("Provide an image name or a docker tarball artifact.");
        return;
      }
    }
    if (kind === "docker_compose") {
      if (!artifactId) {
        setError("Choose a compose artifact for this release.");
        return;
      }
      if (!projectName.trim()) {
        setError("Project name is required.");
        return;
      }
    }

    let spec: Record<string, unknown>;
    if (kind === "systemd") {
      spec = { unit_name: unitName.trim(), start: true };
    } else if (kind === "docker") {
      spec = {
        container_name: containerName.trim(),
        ...(image.trim() ? { image: image.trim() } : {}),
      };
    } else {
      spec = { project_name: projectName.trim() };
    }

    setSaving(true);
    try {
      const release = await createRelease(token, organizationId, {
        name: name.trim(),
        version: version.trim(),
        kind,
        artifact_id: artifactId || null,
        description: description.trim() || null,
        spec,
      });
      onCreated(release);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the release.");
    } finally {
      setSaving(false);
    }
  }

  async function onSubmitYaml(event: FormEvent) {
    event.preventDefault();
    setError(null);
    let payload: ReleaseCreatePayload;
    try {
      const raw = parseYamlObject(yamlText);
      if (raw.name != null && typeof raw.name !== "string") {
        setError("YAML name must be a quoted string.");
        return;
      }
      if (raw.version != null && typeof raw.version !== "string") {
        setError("YAML version must be a quoted string.");
        return;
      }
      const kindValue = String(raw.kind ?? "") as ReleaseKind;
      payload = {
        name: (typeof raw.name === "string" ? raw.name : "").trim(),
        version: (typeof raw.version === "string" ? raw.version : "").trim(),
        kind: kindValue,
        artifact_id: raw.artifact_id == null || raw.artifact_id === "" ? null : String(raw.artifact_id),
        description:
          raw.description == null || raw.description === ""
            ? null
            : String(raw.description),
        spec:
          raw.spec && typeof raw.spec === "object" && !Array.isArray(raw.spec)
            ? (raw.spec as Record<string, unknown>)
            : {},
      };
      if (!payload.name || !payload.version || !payload.kind) {
        setError("YAML must include name, version, and kind.");
        return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid YAML.");
      return;
    }
    setSaving(true);
    try {
      const release = await createRelease(token, organizationId, payload);
      onCreated(release);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the release.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-labelledby="create-release-title"
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <h2 id="create-release-title" className="text-xl font-semibold">
          Create release
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A version of software you can deploy to devices later. Choose how it runs on the device;
          more kinds (for example AI models) can be added without changing this flow.
        </p>
        <div className="mt-4 flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={mode === "form" ? "default" : "outline"}
            onClick={() => setMode("form")}
          >
            Form
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
          <form className="mt-4 space-y-4" onSubmit={(event) => void onSubmitYaml(event)}>
            <div className="space-y-2">
              <Label htmlFor="release-yaml">Release body (YAML)</Label>
              <textarea
                id="release-yaml"
                className="min-h-[240px] w-full rounded-md border border-input bg-field px-3 py-2 font-mono text-sm"
                value={yamlText}
                onChange={(event) => setYamlText(event.target.value)}
                spellCheck={false}
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving}>
                {saving ? "Creating…" : "Create release"}
              </Button>
            </div>
          </form>
        ) : (
        <form className="mt-4 space-y-4" onSubmit={onSubmit}>
          <div className="space-y-2">
            <Label htmlFor="release-kind">Deployment type</Label>
            <select
              id="release-kind"
              className={SELECT_CLASS}
              value={kind}
              onChange={(event) => setKind(event.target.value as ReleaseKind)}
            >
              {RELEASE_KINDS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">{kindMeta.hint}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="release-name">Name</Label>
            <Input
              id="release-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="camera-agent"
              required
              maxLength={120}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="release-version">Version</Label>
            <Input
              id="release-version"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder="1.2.0"
              required
              maxLength={64}
            />
          </div>

          {(artifactRequired || kind === "docker") && (
            <div className="space-y-2">
              <Label htmlFor="release-artifact">
                Artifact{kind === "docker" ? " (optional tarball)" : ""}
              </Label>
              <select
                id="release-artifact"
                className={SELECT_CLASS}
                value={artifactId}
                onChange={(event) => setArtifactId(event.target.value)}
                required={artifactRequired}
              >
                <option value="">
                  {kind === "docker" ? "None — pull image by name" : "Select an artifact…"}
                </option>
                {artifactOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              {artifactsQuery.isSuccess && artifacts.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  {kind === "docker"
                    ? "No docker tarball uploaded. You can still pull by image name below."
                    : `Upload a matching artifact under Artifacts first (type: ${artifactType}).`}
                </p>
              )}
            </div>
          )}

          {kind === "systemd" && (
            <div className="space-y-2">
              <Label htmlFor="release-unit">Unit name</Label>
              <Input
                id="release-unit"
                value={unitName}
                onChange={(event) => setUnitName(event.target.value)}
                placeholder="camera-agent.service"
                required
                maxLength={255}
              />
            </div>
          )}

          {kind === "docker" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="release-container">Container name</Label>
                <Input
                  id="release-container"
                  value={containerName}
                  onChange={(event) => setContainerName(event.target.value)}
                  placeholder="camera-agent"
                  required
                  maxLength={255}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="release-image">Image{artifactId ? " (optional)" : ""}</Label>
                <Input
                  id="release-image"
                  value={image}
                  onChange={(event) => setImage(event.target.value)}
                  placeholder="ghcr.io/example/camera-agent:1.2.0"
                  maxLength={512}
                />
              </div>
            </>
          )}

          {kind === "docker_compose" && (
            <div className="space-y-2">
              <Label htmlFor="release-project">Application / project name</Label>
              <Input
                id="release-project"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="camera-stack"
                required
                maxLength={255}
              />
              <p className="text-xs text-muted-foreground">
                Used as the Docker Compose project name on the device.
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="release-description">Description (optional)</Label>
            <Input
              id="release-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={2000}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving ? "Creating…" : "Create release"}
            </Button>
          </div>
        </form>
        )}
      </div>
    </div>
  );
}
