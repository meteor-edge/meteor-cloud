import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createDeployment, listDeviceGroups, listDevices, listReleases } from "@/api/fleet";
import { DeployWizard } from "@/components/deployments/DeployWizard";

vi.mock("@/api/fleet", () => ({
  createDeployment: vi.fn(),
  listDeviceGroups: vi.fn(),
  listDevices: vi.fn(),
  listReleases: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(listReleases).mockResolvedValue({
    items: [{ id: "rel-1", name: "camera-agent", version: "1.2.0", kind: "systemd", spec: {} }],
    total: 1,
    page: 1,
    page_size: 100,
  } as never);
  vi.mocked(listDevices).mockResolvedValue({
    items: [{ id: "dev-1", name: "edge-01", metadata: {} }],
    total: 1,
    page: 1,
    page_size: 100,
  } as never);
  vi.mocked(listDeviceGroups).mockResolvedValue([]);
  vi.mocked(createDeployment).mockReset();
  vi.mocked(createDeployment).mockResolvedValue({ id: "dep-1" } as never);
});

async function openReview() {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DeployWizard token="t" organizationId="org-1" onClose={vi.fn()} onCreated={vi.fn()} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: /camera-agent 1.2.0/ });
  await user.selectOptions(screen.getByLabelText("Release"), "rel-1");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await user.click(await screen.findByRole("checkbox", { name: "edge-01" }));
  await user.click(screen.getByRole("button", { name: "Next" }));
  return user;
}

describe("DeployWizard", () => {
  it("sends the 1 / 10% / 100% phased preset", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.selectOptions(screen.getByLabelText("Phases"), "1 / 10% / 100%");
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    expect(createDeployment).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createDeployment).mock.calls[0][2].rollout).toEqual({
      strategy: "phased",
      phases: [
        { count: 1 },
        { percent: 10, delay_seconds: 3600 },
        { percent: 100, delay_seconds: 86400 },
      ],
      failure_threshold_percent: 10,
      pending_timeout_seconds: 86400,
    });
  });

  it("sends the canary preset with advanced settings", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.click(screen.getByText("Advanced"));
    await user.type(screen.getByLabelText("Max concurrent"), "25");
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    expect(vi.mocked(createDeployment).mock.calls[0][2].rollout).toEqual({
      strategy: "phased",
      phases: [{ count: 1 }, { percent: 100, delay_seconds: 3600 }],
      failure_threshold_percent: 10,
      max_concurrent: 25,
      pending_timeout_seconds: 86400,
    });
  });

  it("keeps the YAML editor in sync with the wizard", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    const yaml = (screen.getByLabelText("Deploy body (YAML)") as HTMLTextAreaElement).value;
    expect(yaml).toContain("release_id: rel-1");
    expect(yaml).toContain("strategy: phased");
    expect(yaml).toContain("delay_seconds: 3600");

    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(vi.mocked(createDeployment).mock.calls[0][2].rollout).toMatchObject({
      strategy: "phased",
      phases: [{ count: 1 }, { percent: 100, delay_seconds: 3600 }],
      failure_threshold_percent: 10,
    });
  });
});
