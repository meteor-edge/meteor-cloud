import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppAddressPicker } from "@/components/fleet/AppAddressPicker";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return { ...actual, suggestHostname: vi.fn(), checkHostname: vi.fn() };
});

import { checkHostname, suggestHostname } from "@/api/fleet";

const suggestMock = vi.mocked(suggestHostname);
const checkMock = vi.mocked(checkHostname);
const DOMAIN = "demo.meteor-edge.com";
const SUGGESTED = `shop.kiosk-1.acme.${DOMAIN}`;

function renderPicker(onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AppAddressPicker
        token="t"
        organizationId="org-1"
        deviceId="dev-1"
        appName="Shop"
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  return onChange;
}

describe("AppAddressPicker", () => {
  beforeEach(() => {
    suggestMock.mockReset();
    checkMock.mockReset();
    suggestMock.mockResolvedValue({ apps_domain: DOMAIN, hostname: SUGGESTED, available: true });
  });

  it("offers the suggested app.device.org address", async () => {
    const onChange = renderPicker();
    expect(await screen.findByText(SUGGESTED)).toBeInTheDocument();
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(SUGGESTED));
    expect(suggestMock).toHaveBeenCalledWith("t", "org-1", "dev-1", "Shop");
  });

  it("checks a custom name and only accepts it when available", async () => {
    checkMock.mockImplementation(async (_t, _o, _d, hostname) =>
      hostname === `taken.${DOMAIN}`
        ? {
            hostname,
            available: false,
            reason: "taken",
            message: "This address is already in use.",
          }
        : { hostname, available: true, reason: "available", message: "Available." },
    );
    const onChange = renderPicker();
    await screen.findByText(SUGGESTED);
    const input = screen.getByRole("textbox", { name: /custom name/i });

    await userEvent.type(input, "taken");
    expect(await screen.findByText(/already in use/i)).toBeInTheDocument();
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(null));

    await userEvent.clear(input);
    await userEvent.type(input, "hello");
    expect(await screen.findByText("Available.")).toBeInTheDocument();
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(`hello.${DOMAIN}`));
  });
});
