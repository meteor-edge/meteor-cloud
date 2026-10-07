import { FormEvent, useState } from "react";

import { updateDevice, type Device, type DeviceGroup, type DeviceType } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const SELECT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-field text-foreground px-3 text-sm";

export function DeviceEditDialog({
  token,
  organizationId,
  device,
  deviceTypes,
  deviceGroups,
  onClose,
  onSaved,
}: {
  token: string;
  organizationId: string;
  device: Device;
  deviceTypes: DeviceType[];
  deviceGroups: DeviceGroup[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(device.name);
  const [typeId, setTypeId] = useState(device.device_type_id ?? "");
  const [groupId, setGroupId] = useState(device.device_group_id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await updateDevice(token, organizationId, device.id, {
        name,
        ...(typeId ? { device_type_id: typeId } : { clear_device_type: true }),
        ...(groupId ? { device_group_id: groupId } : { clear_device_group: true }),
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the device.");
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Edit ${device.name}`}
    >
      <form
        className="w-full max-w-md space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
        onSubmit={onSubmit}
      >
        <h2 className="text-lg font-semibold">Edit {device.name}</h2>
        <div>
          <Label htmlFor="edit-device-name">Name</Label>
          <Input
            id="edit-device-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </div>
        <div>
          <Label htmlFor="edit-device-type">Device type</Label>
          <select
            id="edit-device-type"
            className={SELECT_CLASS}
            value={typeId}
            onChange={(event) => setTypeId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {deviceTypes.map((type) => (
              <option key={type.id} value={type.id}>
                {type.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="edit-device-group">Device group</Label>
          <select
            id="edit-device-group"
            className={SELECT_CLASS}
            value={groupId}
            onChange={(event) => setGroupId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {deviceGroups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </div>
        {error && (
          <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>
            Save
          </Button>
        </div>
      </form>
    </div>
  );
}
