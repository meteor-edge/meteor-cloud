import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDeployment,
  listDeviceGroups,
  listDevices,
  listEndpoints,
  listReleases,
  suggestHostname,
} from "@/api/fleet";
import { DeployWizard } from "@/components/deployments/DeployWizard";

vi.mock("@/api/fleet", () => ({
  createDeployment: vi.fn(),
  listDeviceGroups: vi.fn(),
  listEndpoints: vi.fn(),
  suggestHostname: vi.fn(),
  checkHostname: vi.fn(),
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
  vi.mocked(listEndpoints).mockResolvedValue([]);
  vi.mocked(suggestHostname).mockResolvedValue({
    apps_domain: "apps.example.com",
    hostname: "web.edge-01.acme.apps.example.com",
    available: true,
  } as never);
  vi.mocked(createDeployment).mockReset();
  vi.mocked(createDeployment).mockResolvedValue({ id: "dep-1" } as never);
});

function useDockerRelease(ports: string[]) {
  vi.mocked(listReleases).mockResolvedValue({
    items: [
      {
        id: "rel-1",
        name: "camera-agent",
        version: "1.2.0",
        kind: "docker",
        spec: { container_name: "web", image: "nginx:alpine", ports },
      },
    ],
    total: 1,
    page: 1,
    page_size: 100,
  } as never);
}

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
      max_concurrent: 50,
      pending_timeout_seconds: 86400,
    });
  });

  it("sends the canary preset with advanced settings", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.click(screen.getByText("Advanced"));
    await user.clear(screen.getByLabelText("Max concurrent"));
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

  it("keeps edited YAML when switching to the wizard and back", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    const editor = screen.getByLabelText("Deploy body (YAML)");
    const custom = [
      "release_id: rel-1",
      "target:",
      "  device_ids: [dev-1]",
      "rollout:",
      "  strategy: Phased",
      "  phases:",
      "    - count: 2",
      "    - percent: 100",
      "      delay_seconds: 600",
      "  failure_threshold_percent: 5",
    ].join("\n");
    await user.clear(editor);
    await user.click(editor);
    await user.paste(custom);

    await user.click(screen.getByRole("button", { name: "Wizard" }));
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    expect((screen.getByLabelText("Deploy body (YAML)") as HTMLTextAreaElement).value).toBe(custom);

    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(vi.mocked(createDeployment).mock.calls[0][2].rollout).toMatchObject({
      strategy: "phased",
      phases: [{ count: 2 }, { percent: 100, delay_seconds: 600 }],
      failure_threshold_percent: 5,
    });
  });

  it("rejects a non-numeric rollout value in YAML", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    const editor = screen.getByLabelText("Deploy body (YAML)") as HTMLTextAreaElement;
    const broken = editor.value.replace("failure_threshold_percent: 10", "failure_threshold_percent: bad");
    expect(broken).not.toBe(editor.value);
    await user.clear(editor);
    await user.click(editor);
    await user.paste(broken);

    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText('Expected a number, got "bad".')).toBeInTheDocument();
    expect(createDeployment).not.toHaveBeenCalled();
  });

  it("fills the YAML from the wizard again on request", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    const editor = screen.getByLabelText("Deploy body (YAML)") as HTMLTextAreaElement;
    await user.type(editor, "\n# note");
    await user.click(screen.getByRole("button", { name: "Wizard" }));
    await user.click(screen.getByRole("button", { name: "Phased" }));
    await user.click(screen.getByRole("button", { name: "Advanced YAML" }));
    const yaml = () => (screen.getByLabelText("Deploy body (YAML)") as HTMLTextAreaElement).value;
    expect(yaml()).toContain("# note");

    await user.click(screen.getByRole("button", { name: "Fill from wizard" }));
    expect(yaml()).toContain("strategy: phased");
    expect(yaml()).not.toContain("# note");
  });

  it("explains the selected phased preset", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Phased" }));
    expect(screen.getByText(/one device installs first/i)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Phases"), "1 / 10% / 100%");
    expect(screen.getByText(/then 10% of the targets/i)).toBeInTheDocument();
    expect(screen.queryByText(/one device installs first/i)).not.toBeInTheDocument();
  });

  it("starts batched with a default batch size", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Batched" }));
    expect(screen.getByLabelText("Batch size")).toHaveValue(5);
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(vi.mocked(createDeployment).mock.calls[0][2].rollout).toEqual({
      strategy: "batched",
      batch_size: 5,
    });
  });

  it("prefills the docker host port and blocks a container port", async () => {
    useDockerRelease(["8080:80"]);
    const user = await openReview();
    await user.click(screen.getByRole("checkbox", { name: "Publish on the internet" }));
    const port = screen.getByLabelText("Port the app listens on");
    expect(port).toHaveValue(8080);

    await user.clear(port);
    await user.type(port, "80");
    expect(screen.getByText(/publishes host port 8080/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(createDeployment).not.toHaveBeenCalled();
  });

  it("blocks a port another app on the device already publishes", async () => {
    useDockerRelease(["9000:80"]);
    vi.mocked(listEndpoints).mockResolvedValue([
      { workload_key: "seed-compose", hostname: "shop.example.com", port: 9000, enabled: true },
    ] as never);
    const user = await openReview();
    await user.click(screen.getByRole("checkbox", { name: "Publish on the internet" }));
    expect(await screen.findByText(/already published on this device for seed-compose/)).toBeInTheDocument();
  });

  it("keeps the existing address when the app is already published", async () => {
    useDockerRelease(["8080:80"]);
    vi.mocked(listEndpoints).mockResolvedValue([
      { workload_key: "web", hostname: "web.example.com", port: 8080, enabled: true },
    ] as never);
    const user = await openReview();
    await user.click(screen.getByRole("checkbox", { name: "Publish on the internet" }));
    expect(await screen.findByText("web.example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(vi.mocked(createDeployment).mock.calls[0][2].endpoint).toEqual({
      hostname: "web.example.com",
      port: 8080,
      protocol: "http",
    });
  });

  it("does not offer publishing for a systemd release", async () => {
    await openReview();
    expect(screen.queryByRole("checkbox", { name: "Publish on the internet" })).not.toBeInTheDocument();
    expect(screen.getByText(/available for single container and compose releases/i)).toBeInTheDocument();
  });
});
