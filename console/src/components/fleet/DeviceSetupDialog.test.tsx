import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createDeviceConfig, createEnrollmentKey, type Artifact } from "@/api/fleet";
import { DeviceSetupDialog } from "@/components/fleet/DeviceSetupDialog";

vi.mock("@/api/fleet", () => ({ createDeviceConfig: vi.fn(), createEnrollmentKey: vi.fn() }));

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
  URL.createObjectURL = vi.fn(() => "blob:settings");
  URL.revokeObjectURL = vi.fn();
});

function setup(props: Partial<Parameters<typeof DeviceSetupDialog>[0]> = {}) {
  const onDownloadImage = vi.fn();
  render(
    <DeviceSetupDialog
      token="token"
      organizationId="org-1"
      canCreateKey
      onClose={vi.fn()}
      onDownloadImage={onDownloadImage}
      {...props}
    />,
  );
  return { user: userEvent.setup(), onDownloadImage };
}

describe("DeviceSetupDialog", () => {
  it("makes a key and downloads the settings the way Meteor Imager writes them", async () => {
    const { user } = setup();
    await user.type(screen.getByLabelText(/MeteorCloud address/), "fleet.example.com");
    await user.click(screen.getByRole("checkbox", { name: "Connect to Wi-Fi" }));
    await user.type(screen.getByLabelText(/Network name/), "Shop floor");
    await user.type(screen.getByLabelText("Wi-Fi password"), "wifi-password-123");
    await user.type(screen.getByLabelText("Country"), "de");
    await user.click(screen.getByRole("checkbox", { name: "Set username and password" }));
    await user.type(screen.getByLabelText("Username"), "ops");
    await user.type(screen.getByLabelText("Password"), "login-password");
    await user.click(screen.getByRole("checkbox", { name: "Enable SSH" }));
    await user.clear(screen.getByLabelText(/Timezone/));
    await user.click(screen.getByRole("button", { name: "Download settings file" }));

    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalled());
    expect(createEnrollmentKey).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createDeviceConfig).mock.calls[0][2]).toEqual({
      enrollment: { domain: "fleet.example.com", api_key: "key_made-for-this-file" },
      wifi: { ssid: "Shop floor", psk: "wifi-password-123", country: "DE", hidden: false },
      user: { name: "ops", password: "login-password" },
      ssh: { enabled: true, password_authentication: true },
    });
    expect(await screen.findByText(/meteorenv.txt downloaded/)).toBeInTheDocument();

    // A second download reuses the key instead of making another.
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    await waitFor(() => expect(createDeviceConfig).toHaveBeenCalledTimes(2));
    expect(createEnrollmentKey).toHaveBeenCalledTimes(1);
  });

  it("says what is missing before calling the server", async () => {
    const { user } = setup({ canCreateKey: false });
    await user.click(screen.getByRole("checkbox", { name: "Connect to Wi-Fi" }));
    await user.click(screen.getByRole("button", { name: "Download settings file" }));
    expect(screen.getByText(/Enter an enrollment key/)).toBeInTheDocument();
    expect(screen.getByText(/Wi-Fi password must be 8 to 63/)).toBeInTheDocument();
    expect(createDeviceConfig).not.toHaveBeenCalled();
    expect(createEnrollmentKey).not.toHaveBeenCalled();
  });

  it("offers the image next to the settings file when opened from an OS image", async () => {
    const { user, onDownloadImage } = setup({ image });
    expect(screen.getByRole("dialog", { name: "Download Meteor OS 1.0" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download image" }));
    expect(onDownloadImage).toHaveBeenCalledWith(image);
  });
});
