import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "@care-e/api-client";
import { App, config } from "./app";

const ORG_TYPES = ["HOSPITAL", "SUPPLIER", "LOGISTICS", "PLATFORM"];

/** Signs in a user of `orgType`; the fake hub answers `/auth/me` (and nothing else). */
function signInAs(orgType: string, capabilities = ["po.respond", "source_request.respond"]) {
  useAuth.setState({ tokens: { access: "a", refresh: "r" } });
  const me = {
    user: { id: "u1", email: "t@x.demo", full_name: "Test User", org_id: "o1", is_active: true },
    org: {
      id: "o1",
      name: `Test ${orgType} org`,
      type: orgType,
      status: "ACTIVE",
      location: { lat: 0, lng: 0 },
    },
    roles: [],
    capabilities,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(me)),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useAuth.setState({ tokens: null });
  window.history.replaceState(null, "", "/");
});

describe(config.name, () => {
  it("asks a signed-out visitor to sign in", () => {
    render(<App />);
    expect(screen.getByText(`Sign in to ${config.name}`)).toBeTruthy();
  });

  it.each(config.allow)("shows a %s user their name, org and org type", async (orgType) => {
    signInAs(orgType);
    render(<App />);
    expect(await screen.findByText(`Test ${orgType} org`)).toBeTruthy();
    expect(screen.getByText("Test User")).toBeTruthy();
    expect(screen.getByText(orgType)).toBeTruthy();
    for (const { label } of config.nav)
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
  });

  it("hides purchase orders and network demand from a user without po.respond", async () => {
    signInAs("SUPPLIER", ["shipment.assign"]);
    render(<App />);
    expect(await screen.findByRole("link", { name: "Dashboard" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Catalog and offers" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Purchase orders" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Network demand" })).toBeNull();
  });

  it("turns on live updates instead of polling", () => {
    expect(config.liveUpdates).toBe(true);
  });

  it.each(ORG_TYPES.filter((t) => !config.allow.includes(t)))(
    "refuses a %s user",
    async (orgType) => {
      signInAs(orgType);
      render(<App />);
      expect(await screen.findByText(config.refusal)).toBeTruthy();
      expect(screen.queryByRole("navigation")).toBeNull();
    },
  );
});
