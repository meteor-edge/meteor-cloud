import { FormEvent, useState } from "react";

import { setArtifactDeviceTypes, type Artifact, type DeviceType } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { artifactDeviceTypeIds } from "@/components/artifacts/artifactTypes";
import { DeviceTypeChecklist } from "@/components/artifacts/DeviceTypeChecklist";
import { Button } from "@/components/ui/button";

type ArtifactDeviceTypesDialogProps = {
  token: string;
  organizationId: string;
  artifact: Artifact;
  deviceTypes: DeviceType[];
  onClose: () => void;
  onSaved: () => void;
};

/** Change which device types an uploaded artifact is for. */
export function ArtifactDeviceTypesDialog({
  token,
  organizationId,
  artifact,
  deviceTypes,
  onClose,
  onSaved,
}: ArtifactDeviceTypesDialogProps) {
  const [selected, setSelected] = useState<string[]>(artifactDeviceTypeIds(artifact));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const title = `Device types for ${artifact.name} ${artifact.version}`;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await setArtifactDeviceTypes(token, organizationId, artifact.id, selected);
      onSaved();
    } catch (err) {
      setSaving(false);
      setError(err instanceof ApiError ? err.message : "Could not change the device types.");
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <form
        className="w-full max-w-md space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
        onSubmit={onSubmit}
      >
        <h2 className="text-lg font-semibold">{title}</h2>
        <DeviceTypeChecklist
          deviceTypes={deviceTypes}
          selected={selected}
          onChange={setSelected}
          disabled={saving}
        />
        {error && (
          <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </div>
  );
}
