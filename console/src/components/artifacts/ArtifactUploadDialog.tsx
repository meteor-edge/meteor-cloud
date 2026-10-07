import { FormEvent, useEffect, useRef, useState } from "react";

import { uploadArtifact, type Artifact, type ArtifactType, type DeviceType } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { ARTIFACT_TYPE_LABELS, ARTIFACT_TYPES } from "@/components/artifacts/artifactTypes";
import { UploadProgress, type UploadProgressState } from "@/components/artifacts/UploadProgress";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatBytes } from "@/lib/utils";

type ArtifactUploadDialogProps = {
  token: string;
  organizationId: string;
  deviceTypes: DeviceType[];
  defaultType?: ArtifactType;
  /** When set, the artifact is bound to this device type and the picker is hidden. */
  fixedDeviceType?: DeviceType;
  onClose: () => void;
  onUploaded: (artifact: Artifact) => void;
};

/** Speed is averaged over this window so the estimate reacts to changes without jitter. */
const SPEED_WINDOW_MS = 5000;
const MIN_SPEED_SAMPLE_MS = 1000;

const SELECT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-field text-foreground px-3 text-sm";

export function ArtifactUploadDialog({
  token,
  organizationId,
  deviceTypes,
  defaultType = "os_image",
  fixedDeviceType,
  onClose,
  onUploaded,
}: ArtifactUploadDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [type, setType] = useState<ArtifactType>(defaultType);
  const [deviceTypeId, setDeviceTypeId] = useState(fixedDeviceType?.id ?? "");
  const [description, setDescription] = useState("");
  const [progress, setProgress] = useState<UploadProgressState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const samplesRef = useRef<{ at: number; loaded: number }[]>([]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const uploading = progress !== null;

  useEffect(() => {
    if (!uploading) {
      return;
    }
    // Closing or reloading the tab would abort a long upload.
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  function onProgress({ loaded, total }: { loaded: number; total: number }) {
    const now = performance.now();
    const samples = samplesRef.current;
    samples.push({ at: now, loaded });
    while (samples.length > 2 && now - samples[1].at > SPEED_WINDOW_MS) {
      samples.shift();
    }
    const oldest = samples[0];
    const elapsedMs = now - oldest.at;
    const bytesPerSecond =
      elapsedMs >= MIN_SPEED_SAMPLE_MS ? ((loaded - oldest.loaded) * 1000) / elapsedMs : null;
    setProgress({ loaded, total, bytesPerSecond });
  }
  const title = fixedDeviceType
    ? `Upload ${ARTIFACT_TYPE_LABELS[type]} for ${fixedDeviceType.name}`
    : "Upload artifact";

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose a file to upload.");
      return;
    }
    setError(null);
    samplesRef.current = [{ at: performance.now(), loaded: 0 }];
    setProgress({ loaded: 0, total: file.size, bytesPerSecond: null });
    abortRef.current = new AbortController();
    try {
      const artifact = await uploadArtifact(
        token,
        organizationId,
        {
          file,
          name,
          version,
          type,
          device_type_id: deviceTypeId || null,
          description: description || null,
        },
        { onProgress, signal: abortRef.current.signal },
      );
      onUploaded(artifact);
    } catch (err) {
      setProgress(null);
      if (err instanceof ApiError && err.code === "aborted") {
        return;
      }
      setError(err instanceof ApiError ? err.message : "Upload failed.");
    }
  }

  function onCancel() {
    if (uploading && !window.confirm("Cancel this upload? Progress will be lost.")) {
      return;
    }
    abortRef.current?.abort();
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <form
        className="w-full max-w-lg space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
        onSubmit={onSubmit}
      >
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The file is stored in object storage. Size and SHA-256 checksum are computed on upload.
          </p>
        </div>

        <div>
          <Label htmlFor="artifact-file">File</Label>
          <Input
            id="artifact-file"
            type="file"
            disabled={uploading}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              setFile(selected);
              if (selected && !name) {
                setName(selected.name.replace(/\.[^.]+$/, ""));
              }
            }}
          />
          {file && (
            <p className="mt-1 text-xs text-muted-foreground">
              {file.name} · {formatBytes(file.size)}
            </p>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="artifact-name">Name</Label>
            <Input
              id="artifact-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. MeteorCloud OS"
              disabled={uploading}
              required
            />
          </div>
          <div>
            <Label htmlFor="artifact-version">Version</Label>
            <Input
              id="artifact-version"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder="e.g. 1.1.0"
              disabled={uploading}
              required
            />
          </div>
          <div>
            <Label htmlFor="artifact-type">Type</Label>
            <select
              id="artifact-type"
              className={SELECT_CLASS}
              value={type}
              onChange={(event) => setType(event.target.value as ArtifactType)}
              disabled={uploading}
            >
              {ARTIFACT_TYPES.map((value) => (
                <option key={value} value={value}>
                  {ARTIFACT_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          {!fixedDeviceType && (
            <div>
              <Label htmlFor="artifact-device-type">Device type</Label>
              <select
                id="artifact-device-type"
                className={SELECT_CLASS}
                value={deviceTypeId}
                onChange={(event) => setDeviceTypeId(event.target.value)}
                disabled={uploading}
              >
                <option value="">Any (not hardware-specific)</option>
                {deviceTypes.map((deviceType) => (
                  <option key={deviceType.id} value={deviceType.id}>
                    {deviceType.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div>
          <Label htmlFor="artifact-description">Description</Label>
          <Input
            id="artifact-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Optional release notes"
            disabled={uploading}
          />
        </div>

        {progress && file && <UploadProgress fileName={file.name} state={progress} />}

        {error && (
          <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            {uploading ? "Cancel upload" : "Cancel"}
          </Button>
          <Button type="submit" disabled={uploading}>
            {uploading ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </form>
    </div>
  );
}
