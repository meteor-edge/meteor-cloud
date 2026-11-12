import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDeviceConfig,
  createEnrollmentKey,
  listEnrollmentKeys,
  type Artifact,
  type EnrollmentApiKey,
} from "@/api/fleet";
import { DeviceSetupDialog } from "@/components/fleet/DeviceSetupDialog";

vi.mock("@/api/fleet", () => ({
  createDeviceConfig: vi.fn(),
  createEnrollmentKey: vi.fn(),
  listEnrollmentKeys: vi.fn(),
}));

function enrollmentKey(fields: Partial<EnrollmentApiKey>): EnrollmentApiKey {
  return {
    id: "k1",
    organization_id: "org-1",
    name: "Shop batch",
    key_prefix: "key_shop12",
    expires_at: null,
    revoked_at: null,
    last_used_at: null,
    created_by_user_id: "u1",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    ...fields,
  };
}

const image = { id: "img-1", name: "Meteor OS", version: "1.0", type: "os_image" } as Artifact;

beforeEach(() => {
  vi.mocked(createDeviceConfig).mockReset().mockResolvedValue({
    file_name: "meteorenv.txt",
    content: "METEOR_ENV_VERSION=2\n",
  });
  vi.mocked(createEnrollmentKey)
    .mockReset()
    .mockResolvedValue({ api_key: "key_made-for-this-file" } as Awaited<
      ReturnType<typeof createEnrollmentKey>
    >);
  vi.mocked(listEnrollmentKeys).mockReset().mockResolvedValue([]);
  URL.createObjectURL = vi.fn(() => "blob:settings");
  URL.revokeObjectURL = vi.fn();
});

function setup(props: Partial<Parameters<typeof DeviceSetupDialog>[0]> = {}) {
  const onDownloadImage = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DeviceSetupDialog
        token="token"
        organizationId="org-1"
        canCreateKey
        onClose={vi.fn()}
        onDownloadImage={onDownloadImage}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), onDownloadImage };
}

async function enableEnrollment(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    screen.getByRole("checkbox", { name: "Register this device with MeteorCloud on first boot" }),
  );
}

describe("DeviceSetupDialog", () => {
  it("downloads OS settings without MeteorCloud when enrollment stays off", async () => {
    const { user } = setup();
    expect(screen.getByText(/OS settings \(like Raspberry Pi Imager\)/)).toBeInTheDocument();
    expect(screen.getByText(/MeteorCloud \(optional\)/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("SSID"), "Shop floor");
    await user.type(screen.getByLabelText("Wi-Fi password"), "wifi-password-123");
    expect(screen.getByLabelText(/Wireless LAN country/)).toHaveValue("DE");
    expect(screen.getByLabelText("Username")).toHaveValue("pi");
    await user.type(screen.getByLabelText(/^Password$/), "login-password");
    await user.clear(screen.getByLabelText(/Timezone/));
    await user.click(screen.getByRole("button", { name: "Download settings file" }));

    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalled());
    expect(createEnrollmentKey).not.toHaveBeenCalled();
    expect(vi.mocked(createDeviceConfig).mock.calls[0][2]).toEqual({
      wifi: { ssid: "Shop floor", psk: "wifi-password-123", country: "DE", hidden: false },
      user: { name: "pi", password: "login-password" },
      ssh: { enabled: true, password_authentication: true },
    });
    expect(await screen.findByText(/meteorenv.txt downloaded/)).toBeInTheDocument();
  });

  it("makes a key and downloads when MeteorCloud enrollment is turned on", async () => {
    const { user } = setup();
    await enableEnrollment(user);
    await user.type(screen.getByLabelText(/MeteorCloud address/), "fleet.example.com");
    await user.type(screen.getByLabelText("SSID"), "Shop floor");
    await user.type(screen.getByLabelText("Wi-Fi password"), "wifi-password-123");
    await user.type(screen.getByLabelText(/^Password$/), "login-password");
    await user.clear(screen.getByLabelText(/Timezone/));
    await user.click(screen.getByRole("button", { name: "Download settings file" }));

    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalled());
    expect(createEnrollmentKey).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createDeviceConfig).mock.calls[0][2].enrollment).toEqual({
      domain: "fleet.example.com",
      api_key: "key_made-for-this-file",
    });

    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalledTimes(2));
    expect(createEnrollmentKey).toHaveBeenCalledTimes(1);
  });

  it("says what is missing before calling the server", async () => {
    const { user } = setup({ canCreateKey: false });
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    expect(screen.getByText(/Wi-Fi password must be 8 to 63/)).toBeInTheDocument();
    expect(screen.queryByText(/Enter an enrollment key/)).not.toBeInTheDocument();
    expect(createDeviceConfig).not.toHaveBeenCalled();
  });

  it("generates a login password from the button", async () => {
    const { user } = setup();
    const buttons = screen.getAllByRole("button", { name: "Generate" });
    await user.click(buttons[0]);
    expect((screen.getByLabelText(/^Password$/) as HTMLInputElement).value).toHaveLength(16);
  });

  it("lists active keys to pick from, and checks the pasted key is that one", async () => {
    vi.mocked(listEnrollmentKeys).mockResolvedValue([
      enrollmentKey({}),
      enrollmentKey({
        id: "k2",
        name: "Old",
        key_prefix: "key_old123",
        revoked_at: "2026-11-02T00:00:00Z",
      }),
    ]);
    const { user } = setup();
    await enableEnrollment(user);
    // Avoid empty Wi-Fi blocking the enrollment checks.
    await user.click(screen.getByRole("checkbox", { name: "Configure wireless LAN" }));
    await user.click(screen.getByRole("checkbox", { name: "Set username and password" }));
    await user.click(screen.getByRole("checkbox", { name: "Enable SSH" }));
    await user.clear(screen.getByLabelText(/Timezone/));
    await user.type(screen.getByLabelText(/MeteorCloud address/), "fleet.example.com");
    await user.click(screen.getByRole("radio", { name: "Use an enrollment key I have" }));
    const picker = await screen.findByRole("combobox", { name: "Which enrollment key" });
    expect(screen.getByRole("option", { name: "Shop batch · key_shop12…" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Old/ })).not.toBeInTheDocument();

    await user.selectOptions(picker, "k1");
    await user.type(screen.getByLabelText("Enrollment key"), "key_other-key-value");
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    expect(screen.getByText(/This is not the key "Shop batch"/)).toBeInTheDocument();
    expect(createDeviceConfig).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Enrollment key"));
    await user.type(screen.getByLabelText("Enrollment key"), "key_shop12-rest-of-it");
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalled());
    expect(vi.mocked(createDeviceConfig).mock.calls[0][2].enrollment?.api_key).toBe(
      "key_shop12-rest-of-it",
    );
    expect(createEnrollmentKey).not.toHaveBeenCalled();
  });

  it("says when there is no key to pick", async () => {
    vi.mocked(listEnrollmentKeys).mockResolvedValue([
      enrollmentKey({ revoked_at: "2026-11-02T00:00:00Z" }),
    ]);
    const { user } = setup();
    await enableEnrollment(user);
    await user.click(screen.getByRole("radio", { name: "Use an enrollment key I have" }));
    expect(await screen.findByText(/no active enrollment keys/)).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Which enrollment key" }),
    ).not.toBeInTheDocument();
  });

  it("refuses a pasted key that matches no active key", async () => {
    vi.mocked(listEnrollmentKeys).mockResolvedValue([enrollmentKey({})]);
    const { user } = setup();
    await enableEnrollment(user);
    await user.click(screen.getByRole("checkbox", { name: "Configure wireless LAN" }));
    await user.click(screen.getByRole("checkbox", { name: "Set username and password" }));
    await user.click(screen.getByRole("checkbox", { name: "Enable SSH" }));
    await user.clear(screen.getByLabelText(/Timezone/));
    await user.type(screen.getByLabelText(/MeteorCloud address/), "fleet.example.com");
    await user.click(screen.getByRole("radio", { name: "Use an enrollment key I have" }));
    await screen.findByRole("combobox", { name: "Which enrollment key" });
    await user.type(screen.getByLabelText("Enrollment key"), "key_revoked-one");
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    expect(screen.getByText(/No active enrollment key starts like this one/)).toBeInTheDocument();
    expect(createDeviceConfig).not.toHaveBeenCalled();
  });

  it("offers the image next to the settings file when opened from an OS image", async () => {
    const { user, onDownloadImage } = setup({ image });
    expect(screen.getByRole("dialog", { name: "Download Meteor OS 1.0" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download image" }));
    expect(onDownloadImage).toHaveBeenCalledWith(image);
  });
});
