import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearEnrollmentKeyAutoApprove,
  listDeviceGroups,
  listDeviceTypes,
  setEnrollmentKeyAutoApprove,
  type EnrollmentApiKey,
} from "@/api/fleet";
import { AutoApproveRuleDialog } from "@/components/fleet/AutoApproveRuleDialog";
import { describeAutoApproveRule, parseAllowlist } from "@/lib/autoApprove";

vi.mock("@/api/fleet", () => ({
  clearEnrollmentKeyAutoApprove: vi.fn(),
  listDeviceGroups: vi.fn(),
  listDeviceTypes: vi.fn(),
  setEnrollmentKeyAutoApprove: vi.fn(),
}));

function key(fields: Partial<EnrollmentApiKey> = {}): EnrollmentApiKey {
  return {
    id: "k1",
    organization_id: "org-1",
    name: "Factory",
    key_prefix: "key_abc",
    expires_at: null,
    revoked_at: null,
    last_used_at: null,
    created_by_user_id: "u1",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    auto_approve_rule: null,
    ...fields,
  };
}

beforeEach(() => {
  vi.mocked(listDeviceTypes).mockReset().mockResolvedValue([]);
  vi.mocked(listDeviceGroups).mockReset().mockResolvedValue([]);
  vi.mocked(setEnrollmentKeyAutoApprove).mockReset().mockResolvedValue(key());
  vi.mocked(clearEnrollmentKeyAutoApprove).mockReset().mockResolvedValue(key());
});

function setup(enrollmentKey = key()) {
  const onSaved = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AutoApproveRuleDialog
        token="token"
        organizationId="org-1"
        enrollmentKey={enrollmentKey}
        onClose={vi.fn()}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  return { onSaved };
}

describe("parseAllowlist", () => {
  it("reads CSV cells, drops headers, blanks and repeats", () => {
    expect(parseAllowlist('serial,mac\n"SN-1",dc:a6:32:00:00:01\nSN-2; SN-1\n\n')).toEqual([
      "SN-1",
      "dc:a6:32:00:00:01",
      "SN-2",
    ]);
  });
});

describe("describeAutoApproveRule", () => {
  it("summarises a rule for the keys table", () => {
    expect(describeAutoApproveRule(key())).toBe("Off");
    expect(
      describeAutoApproveRule(
        key({
          auto_approve_rule: {
            device_type_id: null,
            device_group_id: null,
            allowlist: ["a", "b"],
            max_devices: 10,
            until: null,
            approved_count: 3,
          },
        }),
      ),
    ).toBe("Allow-list 2 · 3 / 10");
  });
});

describe("AutoApproveRuleDialog", () => {
  it("refuses a rule without an allow-list or a maximum", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(screen.getByText(/Add serials or MACs/)).toBeInTheDocument();
    expect(setEnrollmentKeyAutoApprove).not.toHaveBeenCalled();
  });

  it("saves the allow-list and maximum", async () => {
    const user = userEvent.setup();
    const { onSaved } = setup();
    await user.type(screen.getByLabelText(/Allow-list/), "SN-1{enter}SN-2");
    await user.type(screen.getByLabelText("Maximum devices"), "50");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(setEnrollmentKeyAutoApprove).toHaveBeenCalledWith("token", "org-1", "k1", {
        device_type_id: null,
        device_group_id: null,
        allowlist: ["SN-1", "SN-2"],
        max_devices: 50,
        until: null,
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("adds entries from a CSV file", async () => {
    const user = userEvent.setup();
    setup();
    const file = new File(["serial\nSN-7\nSN-8\n"], "batch.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("Add from CSV"), file);
    await waitFor(() => expect(screen.getByText("2 entries")).toBeInTheDocument());
  });

  it("turns an existing rule off", async () => {
    const user = userEvent.setup();
    const { onSaved } = setup(
      key({
        auto_approve_rule: {
          device_type_id: null,
          device_group_id: null,
          allowlist: [],
          max_devices: 5,
          until: null,
          approved_count: 1,
        },
      }),
    );
    expect(screen.getByText("1 approved so far (counts toward the maximum).")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() =>
      expect(clearEnrollmentKeyAutoApprove).toHaveBeenCalledWith("token", "org-1", "k1"),
    );
    expect(onSaved).toHaveBeenCalled();
  });
});
