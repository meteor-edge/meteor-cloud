import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { listDeployedApps, redeployApp } from "@/api/fleet";
import { DeployedApps } from "@/components/apps/DeployedApps";

vi.mock("@/api/fleet", () => ({ listDeployedApps: vi.fn(), redeployApp: vi.fn() }));
vi.mock("@/components/apps/RunAppSheet", () => ({ RunAppSheet: () => <p>sheet</p> }));

it("updates to a moved or a newer tag and opens the editor", async () => {
  const app = { workload_key: "web", spec: { image: "r/acme/web:1.0" }, repository: "web", tag: "1.0" };
  vi.mocked(listDeployedApps).mockResolvedValue([
    { ...app, tag_moved: true, newer_tag: "2.0" },
  ] as never);
  vi.mocked(redeployApp).mockResolvedValue({} as never);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DeployedApps token="t" organizationId="o" deviceId="d" />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Update 1.0" }));
  await waitFor(() => expect(redeployApp).toHaveBeenLastCalledWith("t", "o", "d", "web", {}));
  await waitFor(() => expect(screen.getByRole("button", { name: "Update to 2.0" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Update to 2.0" }));
  await waitFor(() =>
    expect(redeployApp).toHaveBeenLastCalledWith("t", "o", "d", "web", { image: "web:2.0" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit web" }));
  expect(screen.getByText("sheet")).toBeInTheDocument();
});
