import type { DeviceType } from "@/api/fleet";

type DeviceTypeChecklistProps = {
  deviceTypes: DeviceType[];
  selected: string[];
  onChange: (selected: string[]) => void;
  disabled?: boolean;
};

/** Pick any number of device types; none means the artifact is not hardware-specific. */
export function DeviceTypeChecklist({
  deviceTypes,
  selected,
  onChange,
  disabled = false,
}: DeviceTypeChecklistProps) {
  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  }

  return (
    <fieldset disabled={disabled}>
      <legend className="text-sm font-medium">Device types</legend>
      {deviceTypes.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">
          No device types yet. The artifact will be for any device.
        </p>
      ) : (
        <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-input bg-field p-2">
          {deviceTypes.map((deviceType) => (
            <label key={deviceType.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(deviceType.id)}
                onChange={() => toggle(deviceType.id)}
              />
              {deviceType.name}
            </label>
          ))}
        </div>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        {selected.length === 0
          ? "None selected: any device (not hardware-specific)."
          : `For ${selected.length} device type${selected.length === 1 ? "" : "s"}. One image can fit several boards.`}
      </p>
    </fieldset>
  );
}
