import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  bulkApproveEnrollmentRequests,
  bulkRejectEnrollmentRequests,
  listDeviceGroups,
  listDeviceTypes,
  listDevices,
  listEnrollmentRequests,
  type DeviceEnrollmentRequest,
} from "@/api/fleet";
import { PendingEnrollmentRequests } from "@/components/fleet/PendingEnrollmentRequests";

vi.mock("@/api/fleet", () => ({
  approveEnrollmentRequest: vi.fn(),
  rejectEnrollmentRequest: vi.fn(),
  bulkApproveEnrollmentRequests: vi.fn(),
  bulkRejectEnrollmentRequests: vi.fn(),
  listDeviceGroups: vi.fn(),
  listDeviceTypes: vi.fn(),
  listDevices: vi.fn(),
  listEnrollmentRequests: vi.fn(),
}));

function request(
  id: string,
  fields: Partial<DeviceEnrollmentRequest> = {},
): DeviceEnrollmentRequest {
  return {
    id,
    organization_id: "org-1",
    status: "pending",
    claim_secret_prefix: "clm_x",
    requested_name: null,
    assigned_name: null,
    suggested_name: `pi-${id}`,
    device_type_id: null,
    device_group_id: null,
    serial_number: null,
    mac_addresses: [],
    hostname: null,
    os_name: null,
    os_version: null,
    kernel_version: null,
    architecture: null,
    cpu_model: null,
    cpu_cores: null,
    memory_mb: null,
    reviewed_by_user_id: null,
    reviewed_at: null,
    rejection_reason: null,
    claimed_at: null,
    device_id: null,
    enrollment_key_id: null,
    enrollment_key_name: "Batch",
    enrollment_key_prefix: null,
    expires_at: null,
    created_at: "2026-11-19T09:00:00Z",
    updated_at: "2026-11-19T09:00:00Z",
    ...fields,
  };
}

beforeEach(() => {
  vi.mocked(listEnrollmentRequests)
    .mockReset()
    .mockResolvedValue([
      request("a"),
      request("b"),
      request("c", { status: "approved", auto_approved: true }),
    ]);
  vi.mocked(listDeviceTypes).mockReset().mockResolvedValue([]);
  vi.mocked(listDeviceGroups)
    .mockReset()
    .mockResolvedValue([{ id: "g1", name: "Site A" }] as Awaited<
      ReturnType<typeof listDeviceGroups>
    >);
  vi.mocked(listDevices).mockReset();
  vi.mocked(bulkApproveEnrollmentRequests)
    .mockReset()
    .mockResolvedValue({ updated: [], skipped: [] });
  vi.mocked(bulkRejectEnrollmentRequests)
    .mockReset()
    .mockResolvedValue({ updated: [], skipped: [] });
});

function setup(onError = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PendingEnrollmentRequests organizationId="org-1" token="token" canManage onError={onError} />
    </QueryClientProvider>,
  );
  return { onError };
}

describe("PendingEnrollmentRequests bulk review", () => {
  it("approves every selected pending request with one group", async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText("pi-a");
    expect(screen.getByText("Auto-approved, awaiting device")).toBeInTheDocument();
    expect(screen.queryByLabelText("Select pi-c")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Select all pending requests"));
    const bar = screen.getByRole("region", { name: "Bulk actions" });
    expect(within(bar).getByText("2 selected")).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "Approve selected" }));

    const dialog = screen.getByRole("dialog", { name: "Approve selected devices" });
    await user.selectOptions(within(dialog).getByLabelText("Device group"), "g1");
    await user.click(within(dialog).getByRole("button", { name: "Approve 2" }));

    await waitFor(() =>
      expect(bulkApproveEnrollmentRequests).toHaveBeenCalledWith("token", "org-1", {
        request_ids: ["a", "b"],
        device_type_id: undefined,
        device_group_id: "g1",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument(),
    );
  });

  it("rejects the selected requests and reports skipped ones", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "prompt").mockReturnValue("Wrong site");
    vi.mocked(bulkRejectEnrollmentRequests).mockResolvedValue({
      updated: [],
      skipped: [{ request_id: "b", reason: "not_pending" }],
    });
    const { onError } = setup();
    await user.click(await screen.findByLabelText("Select pi-b"));
    await user.click(screen.getByRole("button", { name: "Reject selected" }));

    await waitFor(() =>
      expect(bulkRejectEnrollmentRequests).toHaveBeenCalledWith("token", "org-1", {
        request_ids: ["b"],
        reason: "Wrong site",
      }),
    );
    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith("1 request was no longer pending and not rejected."),
    );
  });

  it("does nothing when the reject prompt is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "prompt").mockReturnValue(null);
    setup();
    await user.click(await screen.findByLabelText("Select pi-a"));
    await user.click(screen.getByRole("button", { name: "Reject selected" }));
    expect(bulkRejectEnrollmentRequests).not.toHaveBeenCalled();
  });
});
