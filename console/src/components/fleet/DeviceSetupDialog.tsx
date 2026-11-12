import { useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import {
  createDeviceConfig,
  createEnrollmentKey,
  listEnrollmentKeys,
  type Artifact,
  type DeviceConfigPayload,
  type EnrollmentApiKey,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type DeviceSetupDialogProps = {
  token: string;
  organizationId: string;
  /** Owners and admins: a new enrollment key can be made for this file. */
  canCreateKey: boolean;
  /** Set when opened from an OS image: the dialog then also downloads the image. */
  image?: Artifact;
  onDownloadImage?: (image: Artifact) => void;
  onClose: () => void;
};

/** This console's address as a device reaches it; never localhost. */
function defaultDomain(): string {
  const { hostname, host } = window.location;
  return hostname === "localhost" || hostname.startsWith("127.") ? "" : host;
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? "";
  } catch {
    return "";
  }
}

function saveTextFile(fileName: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

const FIELDSET = "space-y-3 rounded-md border border-border p-3";
const SELECT =
  "flex h-10 w-full rounded-md border border-input bg-field px-3 text-sm text-foreground";

function isActive(key: EnrollmentApiKey, now: number): boolean {
  return key.revoked_at === null && (key.expires_at === null || Date.parse(key.expires_at) > now);
}
const HINT = "text-xs text-muted-foreground";

/**
 * Settings for a new device, like Meteor Imager's form, downloaded as one file to copy
 * to the card's boot partition. Secrets are sent to the server only to build the file.
 */
export function DeviceSetupDialog({
  token,
  organizationId,
  canCreateKey,
  image,
  onDownloadImage,
  onClose,
}: DeviceSetupDialogProps) {
  const [useEnroll, setUseEnroll] = useState(true);
  const [domain, setDomain] = useState(defaultDomain);
  const [newKey, setNewKey] = useState(canCreateKey);
  const [apiKey, setApiKey] = useState("");
  const [keyId, setKeyId] = useState("");
  // Made by this dialog: newer than the list, so it is not checked against it.
  const [madeKey, setMadeKey] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [useWifi, setUseWifi] = useState(false);
  const [ssid, setSsid] = useState("");
  const [psk, setPsk] = useState("");
  const [country, setCountry] = useState("DE");
  const [hiddenSsid, setHiddenSsid] = useState(false);
  const [hostname, setHostname] = useState("");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [useUser, setUseUser] = useState(false);
  const [userName, setUserName] = useState("");
  const [userPassword, setUserPassword] = useState("");
  const [useSsh, setUseSsh] = useState(false);
  const [sshPassword, setSshPassword] = useState(true);
  const [sshKey, setSshKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const title = image ? `Download ${image.name} ${image.version}` : "Set up a device";

  // Only names and prefixes: the server keeps no copy of a key, so it still has to be pasted.
  // Without enrollment_key.read this fails and the dialog keeps just the paste box.
  const keysQuery = useQuery({
    queryKey: ["enrollment-keys", organizationId, token],
    queryFn: () => listEnrollmentKeys(token, organizationId),
    enabled: useEnroll && !newKey,
    retry: false,
  });
  const activeKeys = (keysQuery.data ?? []).filter((key) => isActive(key, Date.now()));
  const chosenKey = activeKeys.find((key) => key.id === keyId);

  function problems(): string[] {
    const found: string[] = [];
    if (useEnroll) {
      if (!domain.trim()) found.push("Enter the address devices use to reach MeteorCloud.");
      const pasted = apiKey.trim();
      if (!newKey && !/^key_\S{4,}$/.test(pasted)) {
        found.push("Enter an enrollment key (starts with key_).");
      } else if (!newKey && chosenKey && !pasted.startsWith(chosenKey.key_prefix)) {
        found.push(
          `This is not the key "${chosenKey.name}": that one starts with ${chosenKey.key_prefix}.`,
        );
      } else if (
        !newKey &&
        !chosenKey &&
        pasted !== madeKey &&
        keysQuery.data &&
        !activeKeys.some((key) => pasted.startsWith(key.key_prefix))
      ) {
        found.push("No active enrollment key starts like this one. It may be revoked or expired.");
      }
    }
    if (useWifi) {
      if (!ssid) found.push("Enter the Wi-Fi network name.");
      if (psk.length < 8 || psk.length > 63) {
        found.push("The Wi-Fi password must be 8 to 63 characters.");
      }
      if (!/^[A-Z]{2}$/.test(country.trim().toUpperCase())) {
        found.push("Enter the two-letter Wi-Fi country code, e.g. DE.");
      }
    }
    if (useUser && !userName.trim()) found.push("Enter the username.");
    if (useSsh && !sshPassword && !sshKey.trim()) {
      found.push("Public-key only SSH needs a public key.");
    }
    if (!useEnroll && !useWifi && !useUser && !useSsh && !hostname.trim() && !timezone.trim()) {
      found.push("Nothing to write: turn on at least one setting.");
    }
    return found;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setDone(null);
    const found = problems();
    if (found.length > 0) {
      setError(found.join(" "));
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const payload: DeviceConfigPayload = {};
      if (hostname.trim()) payload.hostname = hostname.trim();
      if (timezone.trim()) payload.timezone = timezone.trim();
      if (useWifi) {
        payload.wifi = { ssid, psk, country: country.trim().toUpperCase(), hidden: hiddenSsid };
      }
      if (useUser) payload.user = { name: userName.trim(), password: userPassword };
      if (useSsh) {
        payload.ssh = {
          enabled: true,
          password_authentication: sshPassword,
          ...(sshKey.trim() ? { authorized_key: sshKey.trim() } : {}),
        };
      }
      if (useEnroll) {
        let key = apiKey.trim();
        if (newKey) {
          const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
          const created = await createEnrollmentKey(token, organizationId, {
            name: `Device setup ${stamp}`,
          });
          key = created.api_key;
          // Keep it for another download from this dialog instead of making a key per click.
          setApiKey(key);
          setMadeKey(key);
          setNewKey(false);
        }
        payload.enrollment = {
          domain: domain.trim(),
          api_key: key,
          ...(deviceName.trim() ? { device_name: deviceName.trim() } : {}),
        };
      }
      const file = await createDeviceConfig(token, organizationId, payload);
      saveTextFile(file.file_name, file.content);
      setDone(file.file_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the settings file.");
    } finally {
      setBusy(false);
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
        className="max-h-[90vh] w-full max-w-2xl space-y-4 overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-glow"
        onSubmit={onSubmit}
        autoComplete="off"
      >
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose the device&apos;s settings here and download them as one file. Copy that file to
            the card and the device sets itself up on first boot, the same way a card written by
            Meteor Imager does.
          </p>
        </div>

        <ol className="list-decimal space-y-1 rounded-md border border-border bg-background p-3 pl-8 text-sm">
          <li>
            {image && onDownloadImage ? (
              <>
                <button
                  type="button"
                  className="font-medium text-link underline"
                  onClick={() => onDownloadImage(image)}
                >
                  Download the image
                </button>{" "}
                and write it to the SD card or disk (Raspberry Pi Imager, balenaEtcher or dd).
              </>
            ) : (
              <>
                Write a MeteorCloud OS image to the SD card or disk (Raspberry Pi Imager,
                balenaEtcher or dd). Images are under Artifacts.
              </>
            )}
          </li>
          <li>Fill in the settings below and download the settings file.</li>
          <li>
            Put the card back in your computer and copy <code>meteorenv.txt</code> to its boot
            partition: the small drive that appears, usually named <code>bootfs</code> or{" "}
            <code>METEORCFG</code>. Keep the file name.
          </li>
          <li>
            Put the card in the device and power it on. It applies the settings, deletes the file
            from the card, and asks to join. Approve it under Devices.
          </li>
        </ol>

        <fieldset className={FIELDSET}>
          <legend className="px-1 text-sm font-medium">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useEnroll}
                onChange={(event) => setUseEnroll(event.target.checked)}
              />
              Register this device with MeteorCloud on first boot
            </label>
          </legend>
          {useEnroll && (
            <>
              <div>
                <Label htmlFor="setup-domain">MeteorCloud address the device connects to</Label>
                <Input
                  id="setup-domain"
                  value={domain}
                  onChange={(event) => setDomain(event.target.value)}
                  placeholder="fleet.example.com or 192.168.0.10:8000"
                  spellCheck={false}
                />
              </div>
              <div className="space-y-1">
                {canCreateKey && (
                  <label className="flex items-center gap-2 text-sm">
                    <input type="radio" checked={newKey} onChange={() => setNewKey(true)} />
                    Create a new enrollment key for this file
                  </label>
                )}
                <label className="flex items-center gap-2 text-sm">
                  <input type="radio" checked={!newKey} onChange={() => setNewKey(false)} />
                  Use an enrollment key I have
                </label>
                {!newKey && keysQuery.isSuccess && activeKeys.length === 0 && (
                  <p className="text-sm text-amber-500">
                    This organization has no active enrollment keys.{" "}
                    {canCreateKey
                      ? "Choose “Create a new enrollment key” above, or make one under Devices, Enrollment keys."
                      : "Ask an owner or admin for one."}
                  </p>
                )}
                {!newKey && activeKeys.length > 0 && (
                  <select
                    aria-label="Which enrollment key"
                    className={SELECT}
                    value={keyId}
                    onChange={(event) => setKeyId(event.target.value)}
                  >
                    <option value="">Choose a key…</option>
                    {activeKeys.map((key) => (
                      <option key={key.id} value={key.id}>
                        {key.name} · {key.key_prefix}…
                      </option>
                    ))}
                  </select>
                )}
                {!newKey && (
                  <Input
                    aria-label="Enrollment key"
                    type="password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={chosenKey ? `${chosenKey.key_prefix}…` : "key_…"}
                    autoComplete="new-password"
                    spellCheck={false}
                  />
                )}
                <p className={HINT}>
                  {!newKey && activeKeys.length > 0
                    ? "MeteorCloud shows a key only once, when it is made, so paste the key itself. "
                    : "Keys are listed under Devices, Enrollment keys. "}
                  Revoke the key when the batch of devices is done.
                </p>
              </div>
              <div>
                <Label htmlFor="setup-device-name">Device name in MeteorCloud (optional)</Label>
                <Input
                  id="setup-device-name"
                  value={deviceName}
                  onChange={(event) => setDeviceName(event.target.value)}
                  placeholder="Automatic: device type and MAC, e.g. raspberrypi5-a1b2c3"
                  maxLength={63}
                  spellCheck={false}
                />
                <p className={HINT}>
                  Leave it empty when the same file goes on several cards, so each device gets its
                  own name.
                </p>
              </div>
            </>
          )}
        </fieldset>

        <fieldset className={FIELDSET}>
          <legend className="px-1 text-sm font-medium">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useWifi}
                onChange={(event) => setUseWifi(event.target.checked)}
              />
              Connect to Wi-Fi
            </label>
          </legend>
          {useWifi && (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="setup-ssid">Network name (SSID)</Label>
                  <Input
                    id="setup-ssid"
                    value={ssid}
                    onChange={(event) => setSsid(event.target.value)}
                    spellCheck={false}
                  />
                </div>
                <div>
                  <Label htmlFor="setup-psk">Wi-Fi password</Label>
                  <Input
                    id="setup-psk"
                    type="password"
                    value={psk}
                    onChange={(event) => setPsk(event.target.value)}
                    autoComplete="new-password"
                  />
                </div>
                <div>
                  <Label htmlFor="setup-country">Country</Label>
                  <Input
                    id="setup-country"
                    value={country}
                    onChange={(event) => setCountry(event.target.value.toUpperCase())}
                    placeholder="DE"
                    maxLength={2}
                  />
                </div>
                <label className="flex items-end gap-2 pb-2 text-sm">
                  <input
                    type="checkbox"
                    checked={hiddenSsid}
                    onChange={(event) => setHiddenSsid(event.target.checked)}
                  />
                  Hidden network
                </label>
              </div>
              <p className={HINT}>
                The Wi-Fi password is in the file and on the card until first boot. Anyone with
                either can read it.
              </p>
            </>
          )}
        </fieldset>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="setup-hostname">Hostname (optional)</Label>
            <Input
              id="setup-hostname"
              value={hostname}
              onChange={(event) => setHostname(event.target.value.toLowerCase())}
              placeholder="raspberrypi"
              spellCheck={false}
            />
          </div>
          <div>
            <Label htmlFor="setup-timezone">Timezone (optional)</Label>
            <Input
              id="setup-timezone"
              value={timezone}
              onChange={(event) => setTimezone(event.target.value)}
              placeholder="Europe/Berlin"
              spellCheck={false}
            />
          </div>
        </div>

        <fieldset className={FIELDSET}>
          <legend className="px-1 text-sm font-medium">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useUser}
                onChange={(event) => setUseUser(event.target.checked)}
              />
              Set username and password
            </label>
          </legend>
          {useUser && (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="setup-user">Username</Label>
                  <Input
                    id="setup-user"
                    value={userName}
                    onChange={(event) => setUserName(event.target.value)}
                    placeholder="pi"
                    spellCheck={false}
                    autoCapitalize="off"
                  />
                </div>
                <div>
                  <Label htmlFor="setup-user-password">Password</Label>
                  <Input
                    id="setup-user-password"
                    type="password"
                    value={userPassword}
                    onChange={(event) => setUserPassword(event.target.value)}
                    autoComplete="new-password"
                  />
                </div>
              </div>
              <p className={HINT}>
                The image&apos;s first user is renamed to this name (or created). Only a hash of
                the password goes into the file.
              </p>
            </>
          )}
        </fieldset>

        <fieldset className={FIELDSET}>
          <legend className="px-1 text-sm font-medium">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useSsh}
                onChange={(event) => setUseSsh(event.target.checked)}
              />
              Enable SSH
            </label>
          </legend>
          {useSsh && (
            <>
              <label className="flex items-center gap-2 text-sm">
                <input type="radio" checked={sshPassword} onChange={() => setSshPassword(true)} />
                Use password authentication
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="radio" checked={!sshPassword} onChange={() => setSshPassword(false)} />
                Allow public-key authentication only
              </label>
              <div>
                <Label htmlFor="setup-ssh-key">
                  SSH public key{sshPassword ? " (optional)" : ""}
                </Label>
                <Input
                  id="setup-ssh-key"
                  value={sshKey}
                  onChange={(event) => setSshKey(event.target.value)}
                  placeholder="ssh-ed25519 AAAA… you@laptop"
                  spellCheck={false}
                />
              </div>
              <p className={HINT}>
                The key is added for the user above, or the image&apos;s first user.
              </p>
            </>
          )}
        </fieldset>

        <p className={HINT}>
          Works with MeteorCloud OS images (the ones that include meteorcli). An image built before
          agent 0.3.0 only reads the name <code>.meteorenv</code>: rename the file on the card.
          Delete the downloaded file from your computer once it is on the card: it holds the
          enrollment key and the Wi-Fi password.
        </p>

        {error && (
          <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
        )}
        {done && (
          <p className="rounded-md border border-border bg-background px-2 py-1 text-sm">
            {done} downloaded. Copy it to the card&apos;s boot partition.
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Close
          </Button>
          {image && onDownloadImage && (
            <Button type="button" variant="secondary" onClick={() => onDownloadImage(image)}>
              Download image
            </Button>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? "Preparing…" : "Download settings file"}
          </Button>
        </div>
      </form>
    </div>
  );
}
