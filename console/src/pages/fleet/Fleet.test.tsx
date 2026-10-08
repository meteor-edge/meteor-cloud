import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/auth";
import * as fleetApi from "@/api/fleet";
import * as orgApi from "@/api/organizations";
import { App } from "@/App";
import { AuthProvider } from "@/auth/AuthContext";
import { OrganizationProvider } from "@/context/OrganizationContext";

vi.mock("@/api/auth");
vi.mock("@/api/organizations");
vi.mock("@/api/fleet");

function renderApp(initialEntries: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <OrganizationProvider>
            <App />
          </OrganizationProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ownerUser = {
  id: "user-1",
  email: "owner@example.com",
  full_name: "Owner",
  is_active: true,
  created_at: new Date().toISOString(),
};

function org(role: "owner" | "viewer") {
  return {
    id: "org-1",
    name: "Acme Energy",
    slug: "acme-energy",
    description: null,
    created_by_user_id: "user-1",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    current_user_role: role,
    member_count: 1,
  };
}

const deviceType = {
  id: "type-1",
  organization_id: "org-1",
  name: "Gateway",
  slug: "gateway",
  description: null,
  manufacturer: "Raspberry Pi",
  model: "4B",
  architecture: "arm64",
  capabilities: {},
  metadata: {},
  device_count: 1,
  artifact_count: 1,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const deviceGroup = {
  id: "group-1",
  organization_id: "org-1",
  name: "Berlin",
  slug: "berlin",
  description: null,
  labels: {},
  metadata: {},
  device_count: 3,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const osImage = {
  id: "artifact-1",
  organization_id: "org-1",
  device_type_id: "type-1",
  name: "Raspberry Pi OS",
  version: "1.0.0",
  type: "os_image" as const,
  description: null,
  file_name: "rpi-os.img",
  content_type: "application/octet-stream",
  size_bytes: 2048,
  checksum_sha256: "a".repeat(64),
  metadata: {},
  created_by_user_id: "user-1",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const device = {
  id: "device-1",
  organization_id: "org-1",
  name: "edge-01",
  device_type_id: null,
  device_group_id: null,
  is_enabled: true,
  status: "online" as const,
  serial_number: null,
  mac_addresses: ["aa:bb:cc:dd:ee:ff"],
  hostname: "edge-01",
  os_name: "Ubuntu",
  os_version: "22.04",
  kernel_version: "6.2.0",
  architecture: "x86_64",
  cpu_model: "Intel",
  cpu_cores: 4,
  memory_mb: 8000,
  labels: {},
  metadata: {},
  credential_prefix: "dev_abc",
  last_seen_at: new Date().toISOString(),
  registered_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  mqtt_configured: true,
  mqtt_status: "online" as const,
  mqtt_status_at: new Date().toISOString(),
  mqtt_metrics: {
    cpu_percent: 18.4,
    memory_percent: 42.1,
    disk_percent: 61.3,
    temperature_c: 54.2,
  },
  mqtt_metrics_at: new Date().toISOString(),
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("edge_platform_access_token", "token-123");
  vi.resetAllMocks();
  vi.mocked(authApi.fetchCurrentUser).mockResolvedValue(ownerUser);
  vi.mocked(orgApi.listOrganizations).mockResolvedValue([org("owner")]);
  vi.mocked(orgApi.getOrganization).mockResolvedValue(org("owner"));
  vi.mocked(fleetApi.listDeviceTypes).mockResolvedValue([]);
  vi.mocked(fleetApi.listDeviceGroups).mockResolvedValue([]);
  vi.mocked(fleetApi.listRegistrationTokens).mockResolvedValue([]);
  vi.mocked(fleetApi.listEnrollmentKeys).mockResolvedValue([]);
  vi.mocked(fleetApi.listEnrollmentRequests).mockResolvedValue([]);
  vi.mocked(fleetApi.listDevices).mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 10,
  });
  vi.mocked(fleetApi.listenMqttEvents).mockReturnValue(() => {});
  vi.mocked(fleetApi.publishMqttTest).mockResolvedValue({
    topic: "devices/device-1/events",
    payload: "hello from console",
  });
});

describe("Device types", () => {
  it("creates a device type as owner", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.createDeviceType).mockResolvedValue(deviceType);

    renderApp(["/organizations/org-1/device-types"]);
    await user.click(await screen.findByRole("button", { name: /add device type/i }));
    await user.type(screen.getByLabelText(/^name$/i), "Gateway");
    await user.type(screen.getByLabelText(/^architecture$/i), "arm64");
    await user.click(screen.getByRole("button", { name: /create device type/i }));

    await waitFor(() => {
      expect(fleetApi.createDeviceType).toHaveBeenCalledWith("token-123", "org-1", {
        name: "Gateway",
        manufacturer: undefined,
        model: undefined,
        architecture: "arm64",
        description: undefined,
      });
    });
  });

  it("hides create form for viewers", async () => {
    vi.mocked(orgApi.getOrganization).mockResolvedValue(org("viewer"));
    vi.mocked(fleetApi.listDeviceTypes).mockResolvedValue([deviceType]);

    renderApp(["/organizations/org-1/device-types"]);
    expect(await screen.findByText("Gateway")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add device type/i })).not.toBeInTheDocument();
  });

  it("uploads an OS image from the device type artifacts tab", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.getDeviceType).mockResolvedValue(deviceType);
    vi.mocked(fleetApi.listArtifacts).mockResolvedValue({
      items: [osImage],
      total: 1,
      page: 1,
      page_size: 100,
    });
    vi.mocked(fleetApi.uploadArtifact).mockResolvedValue({ ...osImage, id: "artifact-2" });

    renderApp(["/organizations/org-1/device-types/type-1?tab=artifacts"]);
    expect(await screen.findByText("Raspberry Pi OS")).toBeInTheDocument();
    expect(screen.getByText("rpi-os.img")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /upload os image/i }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["image-bytes"], "rpi-os-2.img", { type: "application/octet-stream" });
    await user.upload(within(dialog).getByLabelText(/^file$/i), file);
    await user.type(within(dialog).getByLabelText(/^version$/i), "2.0.0");
    await user.click(within(dialog).getByRole("button", { name: /^upload$/i }));

    await waitFor(() => {
      expect(fleetApi.uploadArtifact).toHaveBeenCalledWith(
        "token-123",
        "org-1",
        expect.objectContaining({
          file,
          version: "2.0.0",
          type: "os_image",
          device_type_id: "type-1",
        }),
        expect.anything(),
      );
    });
  });
});

describe("Device groups", () => {
  it("shows the device count per group", async () => {
    vi.mocked(fleetApi.listDeviceGroups).mockResolvedValue([deviceGroup]);

    renderApp(["/organizations/org-1/device-groups"]);
    expect(await screen.findByText("Berlin")).toBeInTheDocument();
    const card = screen.getByRole("link", { name: /berlin/i });
    expect(card).toHaveTextContent(/3 devices/i);
    expect(card).toHaveAttribute("href", "/organizations/org-1/device-groups/group-1");
  });
});

describe("Device group detail", () => {
  it("shows an error instead of the empty state when devices fail to load", async () => {
    vi.mocked(fleetApi.getDeviceGroup).mockResolvedValue(deviceGroup);
    vi.mocked(fleetApi.listDevices).mockRejectedValue(new Error("boom"));

    renderApp(["/organizations/org-1/device-groups/group-1?tab=devices"]);
    expect(await screen.findByText("Could not load devices.")).toBeInTheDocument();
    expect(screen.queryByText("No devices yet.")).not.toBeInTheDocument();
  });
});

describe("Artifacts", () => {
  it("opens the OS Images view from the menu link", async () => {
    vi.mocked(fleetApi.listArtifacts).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });

    renderApp(["/organizations/org-1/artifacts?type=os_image"]);
    expect(await screen.findByRole("tab", { name: "OS Images" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() => {
      expect(fleetApi.listArtifacts).toHaveBeenCalledWith(
        "token-123",
        "org-1",
        expect.objectContaining({ type: "os_image" }),
      );
    });
  });

  it("shows upload progress for a large image and lets the user cancel", async () => {
    const user = userEvent.setup();
    const GiB = 1024 ** 3;
    let reportProgress: (progress: { loaded: number; total: number }) => void = () => {};
    let uploadSignal: AbortSignal | undefined;
    vi.mocked(fleetApi.listArtifacts).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    vi.mocked(fleetApi.uploadArtifact).mockImplementation((_token, _org, _payload, options) => {
      reportProgress = options?.onProgress ?? reportProgress;
      uploadSignal = options?.signal;
      return new Promise(() => {});
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderApp(["/organizations/org-1/artifacts?type=os_image"]);
    await user.click(await screen.findByRole("button", { name: /upload os image/i }));
    const dialog = await screen.findByRole("dialog");
    await user.upload(
      within(dialog).getByLabelText(/^file$/i),
      new File(["image"], "meteor-os.img", { type: "application/octet-stream" }),
    );
    await user.type(within(dialog).getByLabelText(/^version$/i), "1.0.0");
    await user.click(within(dialog).getByRole("button", { name: /^upload$/i }));

    act(() => reportProgress({ loaded: GiB / 2, total: GiB }));
    const bar = within(dialog).getByRole("progressbar", { name: /upload progress/i });
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(within(dialog).getByText("512.0 MB of 1.0 GB")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /uploading/i })).toBeDisabled();

    act(() => reportProgress({ loaded: GiB, total: GiB }));
    expect(within(dialog).getByText(/saving to storage/i)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /cancel upload/i }));
    expect(uploadSignal?.aborted).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    vi.mocked(window.confirm).mockRestore();
  });

  it("lists artifacts and filters by type and version", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.listDeviceTypes).mockResolvedValue([deviceType]);
    vi.mocked(fleetApi.listArtifacts).mockResolvedValue({
      items: [osImage],
      total: 1,
      page: 1,
      page_size: 20,
    });

    renderApp(["/organizations/org-1/artifacts"]);
    expect(await screen.findByText("Raspberry Pi OS")).toBeInTheDocument();
    const row = screen.getByText("Raspberry Pi OS").closest("tr") as HTMLElement;
    expect(within(row).getByText("OS Image")).toBeInTheDocument();
    expect(within(row).getByText("Gateway")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "OS Images" }));
    expect(screen.getByRole("button", { name: /upload os image/i })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/filter by version/i), "1.0");
    await waitFor(() => {
      expect(fleetApi.listArtifacts).toHaveBeenCalledWith(
        "token-123",
        "org-1",
        expect.objectContaining({ type: "os_image", version: "1.0", page: 1 }),
      );
    });
  });
});

describe("Navigation", () => {
  it("shows the organization menu with devices sub-pages", async () => {
    renderApp(["/organizations/org-1/devices"]);
    const nav = await screen.findByRole("navigation", { name: /main/i });
    await within(nav).findByText("Acme Energy");
    for (const label of [
      "Overview",
      "All Devices",
      "Groups",
      "Types",
      "All Artifacts",
      "OS Images",
      "Monitoring",
      "Members",
      "Teams",
      "Settings",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(within(nav).queryByRole("link", { name: /^fleet$/i })).not.toBeInTheDocument();
    expect(within(nav).getAllByText("Devices")).toHaveLength(1);
    expect(within(nav).getByRole("link", { name: "All Devices" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    const devicesSection = within(nav).getByRole("button", { name: "Devices" });
    expect(devicesSection).toHaveAttribute("aria-expanded", "true");
    await userEvent.setup().click(devicesSection);
    expect(devicesSection).toHaveAttribute("aria-expanded", "false");
    expect(within(nav).queryByRole("link", { name: "Groups" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "OS Images" })).toHaveAttribute(
      "href",
      "/organizations/org-1/artifacts?type=os_image",
    );
    expect(within(nav).getByRole("button", { name: "People" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Teams" })).toHaveAttribute(
      "href",
      "/organizations/org-1/teams",
    );
  });

  it("hides the People menu from viewers", async () => {
    vi.mocked(orgApi.listOrganizations).mockResolvedValue([org("viewer")]);
    renderApp(["/organizations/org-1/devices"]);
    const nav = await screen.findByRole("navigation", { name: /main/i });
    await within(nav).findByText("Acme Energy");
    expect(within(nav).queryByRole("button", { name: "People" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Members" })).not.toBeInTheDocument();
  });
});

describe("Add device flow", () => {
  it("creates a device token and shows the plaintext once with copy", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    vi.mocked(fleetApi.createRegistrationToken).mockResolvedValue({
      id: "tok-1",
      organization_id: "org-1",
      name: "edge-gateway-01",
      token_prefix: "reg_abcdef01",
      device_type_id: null,
      device_group_id: null,
      expires_at: null,
      max_uses: 1,
      use_count: 0,
      revoked_at: null,
      created_by_user_id: "user-1",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      token: "reg_super-secret-value",
    });

    renderApp(["/organizations/org-1/devices"]);
    await user.click(await screen.findByRole("button", { name: /add device/i }));
    await user.type(await screen.findByLabelText(/^name$/i), "edge-gateway-01");
    await user.click(screen.getByRole("button", { name: /create token/i }));

    await waitFor(() => {
      expect(fleetApi.createRegistrationToken).toHaveBeenCalledWith("token-123", "org-1", {
        name: "edge-gateway-01",
        max_uses: 1,
        device_type_id: undefined,
        device_group_id: undefined,
      });
    });

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("reg_super-secret-value")).toBeInTheDocument();

    await user.click(within(dialog).getAllByRole("button", { name: /copy/i })[0]);
    expect(writeText).toHaveBeenCalledWith("reg_super-secret-value");

    await user.click(within(dialog).getByRole("button", { name: /done/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("lists pending device registrations", async () => {
    vi.mocked(fleetApi.listRegistrationTokens).mockResolvedValue([
      {
        id: "tok-2",
        organization_id: "org-1",
        name: "warehouse-sensor",
        token_prefix: "reg_pending1",
        device_type_id: null,
        device_group_id: null,
        expires_at: null,
        max_uses: 1,
        use_count: 0,
        revoked_at: null,
        created_by_user_id: "user-1",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);

    renderApp(["/organizations/org-1/devices"]);
    expect(await screen.findByText("warehouse-sensor")).toBeInTheDocument();
    expect(screen.getByText(/awaiting registration/i)).toBeInTheDocument();
  });

  it("shows pending enrollment requests on the devices page", async () => {
    vi.mocked(fleetApi.listEnrollmentRequests).mockResolvedValue([
      {
        id: "req-1",
        organization_id: "org-1",
        status: "pending",
        claim_secret_prefix: "clm_abcdef",
        requested_name: "edge-01",
        assigned_name: null,
        device_type_id: null,
        device_group_id: null,
        serial_number: null,
        mac_addresses: [],
        hostname: "edge-01",
        os_name: "Ubuntu",
        os_version: null,
        kernel_version: null,
        architecture: "x86_64",
        cpu_model: null,
        cpu_cores: null,
        memory_mb: null,
        reviewed_by_user_id: null,
        reviewed_at: null,
        rejection_reason: null,
        claimed_at: null,
        device_id: null,
        expires_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);

    renderApp(["/organizations/org-1/devices"]);
    expect(
      await screen.findByRole("heading", { name: /pending enrollment requests/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("edge-01").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: /requested/i })).toBeInTheDocument();
  });
});

describe("Devices list", () => {
  it("renders devices and applies search filter", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.listDeviceTypes).mockResolvedValue([deviceType]);
    vi.mocked(fleetApi.listDeviceGroups).mockResolvedValue([deviceGroup]);
    vi.mocked(fleetApi.listDevices).mockResolvedValue({
      items: [{ ...device, device_type_id: "type-1", device_group_id: "group-1" }],
      total: 1,
      page: 1,
      page_size: 10,
    });

    renderApp(["/organizations/org-1/devices"]);
    const deviceLink = await screen.findByRole("link", { name: "edge-01" });
    const row = deviceLink.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Online")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Gateway")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Berlin")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByRole("link", { name: "View" })).toBeInTheDocument();

    await user.type(screen.getByLabelText(/search devices/i), "edge");
    await waitFor(() => {
      expect(fleetApi.listDevices).toHaveBeenCalledWith(
        "token-123",
        "org-1",
        expect.objectContaining({ search: "edge" }),
      );
    });
  });

  it("refreshes devices and pending registrations", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.listDevices).mockResolvedValue({
      items: [device],
      total: 1,
      page: 1,
      page_size: 10,
    });

    renderApp(["/organizations/org-1/devices"]);
    await screen.findByRole("link", { name: "edge-01" });
    const initialDeviceCalls = vi.mocked(fleetApi.listDevices).mock.calls.length;
    const initialTokenCalls = vi.mocked(fleetApi.listRegistrationTokens).mock.calls.length;

    await user.click(screen.getByRole("button", { name: /refresh devices/i }));

    await waitFor(() => {
      expect(vi.mocked(fleetApi.listDevices).mock.calls.length).toBeGreaterThan(initialDeviceCalls);
      expect(vi.mocked(fleetApi.listRegistrationTokens).mock.calls.length).toBeGreaterThan(
        initialTokenCalls,
      );
    });
  });

  it("deletes a device after confirmation", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(fleetApi.listDevices).mockResolvedValue({
      items: [device],
      total: 1,
      page: 1,
      page_size: 10,
    });
    vi.mocked(fleetApi.deleteDevice).mockResolvedValue(undefined);

    renderApp(["/organizations/org-1/devices"]);
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(fleetApi.deleteDevice).toHaveBeenCalledWith("token-123", "org-1", "device-1");
    });
    vi.mocked(window.confirm).mockRestore();
  });

  it("edits a device type and group from the list", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.listDeviceTypes).mockResolvedValue([deviceType]);
    vi.mocked(fleetApi.listDeviceGroups).mockResolvedValue([deviceGroup]);
    vi.mocked(fleetApi.listDevices).mockResolvedValue({
      items: [device],
      total: 1,
      page: 1,
      page_size: 10,
    });
    vi.mocked(fleetApi.updateDevice).mockResolvedValue(device);

    renderApp(["/organizations/org-1/devices"]);
    await user.click(await screen.findByRole("button", { name: /^edit$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(/device type/i), "type-1");
    await user.selectOptions(within(dialog).getByLabelText(/device group/i), "group-1");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(fleetApi.updateDevice).toHaveBeenCalledWith("token-123", "org-1", "device-1", {
        name: "edge-01",
        device_type_id: "type-1",
        device_group_id: "group-1",
      });
    });
  });

  it("hides device delete for viewers", async () => {
    vi.mocked(orgApi.getOrganization).mockResolvedValue(org("viewer"));
    vi.mocked(fleetApi.listDevices).mockResolvedValue({
      items: [device],
      total: 1,
      page: 1,
      page_size: 10,
    });

    renderApp(["/organizations/org-1/devices"]);
    expect(await screen.findByRole("link", { name: "edge-01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^delete$/i })).not.toBeInTheDocument();
  });
});

describe("Device detail", () => {
  it("rotates the credential and shows it once", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.getDevice).mockResolvedValue(device);
    vi.mocked(fleetApi.rotateDeviceCredential).mockResolvedValue({
      device_id: "device-1",
      token: "dev_new-secret",
      credential_prefix: "dev_new",
    });

    renderApp(["/organizations/org-1/devices/device-1"]);
    await user.click(await screen.findByRole("button", { name: /rotate credential/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("dev_new-secret")).toBeInTheDocument();
  });

  it("sends an MQTT ping from the device detail page", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.getDevice).mockResolvedValue(device);
    vi.mocked(fleetApi.pingDevice).mockResolvedValue({
      command_id: "cmd-1",
      status: "completed",
      round_trip_ms: 120,
      result: { message: "pong" },
      message: null,
    });

    renderApp(["/organizations/org-1/devices/device-1"]);
    expect(await screen.findByText("MQTT Connection")).toBeInTheDocument();
    expect(screen.getAllByText("aa:bb:cc:dd:ee:ff").length).toBeGreaterThan(0);
    expect(screen.getAllByText("devices/device-1/status").length).toBeGreaterThan(0);
    expect(screen.getByText("devices/device-1/metrics")).toBeInTheDocument();
    expect(screen.getByText("18.4")).toBeInTheDocument();
    expect(screen.getByText("42.1")).toBeInTheDocument();
    expect(screen.getByText(/meteorcli mqtt-test/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /test connection/i }));
    expect(await screen.findByText(/connection test successful/i)).toBeInTheDocument();
    expect(screen.getByText(/round trip: 120 ms/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /test connection/i })).toBeEnabled();
  });

  it("disables Test Connection while the ping is in flight", async () => {
    const user = userEvent.setup();
    let resolvePing: (value: fleetApi.DevicePingResult) => void = () => {};
    vi.mocked(fleetApi.getDevice).mockResolvedValue(device);
    vi.mocked(fleetApi.pingDevice).mockImplementation(
      () =>
        new Promise<fleetApi.DevicePingResult>((resolve) => {
          resolvePing = resolve;
        }),
    );

    renderApp(["/organizations/org-1/devices/device-1"]);
    const button = await screen.findByRole("button", { name: /test connection/i });
    await user.click(button);
    expect(button).toBeDisabled();
    resolvePing({
      command_id: "cmd-1",
      status: "completed",
      round_trip_ms: 10,
      result: { message: "pong" },
      message: null,
    });
    expect(await screen.findByText(/connection test successful/i)).toBeInTheDocument();
    expect(button).toBeEnabled();
  });
});

describe("API keys", () => {
  it("creates an API key and shows the plaintext once", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    vi.mocked(fleetApi.createEnrollmentKey).mockResolvedValue({
      id: "key-1",
      organization_id: "org-1",
      name: "Field techs",
      key_prefix: "key_abcdef01",
      expires_at: null,
      revoked_at: null,
      last_used_at: null,
      created_by_user_id: "user-1",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      api_key: "key_super-secret-value",
    });

    renderApp(["/organizations/org-1/api-keys"]);
    await user.type(await screen.findByLabelText(/^name$/i), "Field techs");
    await user.click(screen.getByRole("button", { name: /create api key/i }));

    await waitFor(() => {
      expect(fleetApi.createEnrollmentKey).toHaveBeenCalledWith("token-123", "org-1", {
        name: "Field techs",
      });
    });

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("key_super-secret-value")).toBeInTheDocument();
  });

  it("approves a pending enrollment request", async () => {
    const user = userEvent.setup();
    const pendingRequest = {
      id: "req-1",
      organization_id: "org-1",
      status: "pending" as const,
      claim_secret_prefix: "clm_abcdef",
      requested_name: "edge-warehouse",
      assigned_name: null,
      device_type_id: null,
      device_group_id: null,
      serial_number: null,
      mac_addresses: [],
      hostname: "edge-warehouse",
      os_name: "Ubuntu",
      os_version: null,
      kernel_version: null,
      architecture: "x86_64",
      cpu_model: null,
      cpu_cores: null,
      memory_mb: null,
      reviewed_by_user_id: null,
      reviewed_at: null,
      rejection_reason: null,
      claimed_at: null,
      device_id: null,
      expires_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    vi.mocked(fleetApi.listEnrollmentRequests).mockResolvedValue([pendingRequest]);
    vi.mocked(fleetApi.approveEnrollmentRequest).mockImplementation(async () => {
      const approved = {
        ...pendingRequest,
        status: "approved" as const,
        assigned_name: "edge-warehouse",
        reviewed_by_user_id: "user-1",
        reviewed_at: new Date().toISOString(),
      };
      vi.mocked(fleetApi.listEnrollmentRequests).mockResolvedValue([approved]);
      return approved;
    });

    renderApp(["/organizations/org-1/api-keys"]);
    expect(await screen.findByRole("columnheader", { name: /requested/i })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^approve$/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^approve$/i }));
    await user.click(screen.getByRole("button", { name: /confirm approval/i }));

    await waitFor(() => {
      expect(fleetApi.approveEnrollmentRequest).toHaveBeenCalledWith(
        "token-123",
        "org-1",
        "req-1",
        expect.objectContaining({ name: "edge-warehouse" }),
      );
    });
    expect(await screen.findByText(/awaiting device/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve$/i })).not.toBeInTheDocument();
  });
});

describe("MQTT test", () => {
  it("sends and shows plain text on a typed topic", async () => {
    const user = userEvent.setup();
    vi.mocked(fleetApi.listenMqttEvents).mockImplementation((_token, _org, topic, onEvent) => {
      onEvent({
        organization_id: "org-1",
        device_id: null,
        topic,
        payload: "23.5",
        received_at: new Date().toISOString(),
      });
      return () => {};
    });

    renderApp(["/organizations/org-1/mqtt"]);
    expect(await screen.findByRole("heading", { name: /mqtt test/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/^device$/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/^topic$/i), "lab/temp");
    await user.click(screen.getByRole("button", { name: /^listen$/i }));
    expect(await screen.findByText("Received")).toBeInTheDocument();
    expect(await screen.findByText("23.5")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^stop$/i }));
    expect(screen.getByRole("button", { name: /^listen$/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^publish$/i }));
    expect(await screen.findByText("Sent")).toBeInTheDocument();
  });
});
