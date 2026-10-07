import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "@care-e/api-client";
import { App, config } from "./app";
import { page, shortage } from "./test/fixtures";

const ORG_TYPES = ["HOSPITAL", "SUPPLIER", "LOGISTICS", "PLATFORM"];
const ALL_CAPS = ["inventory.edit", "shortage.create", "source_request.respond", "audit.read"];

/** Signs in a user of `orgType`; the fake hub answers `/auth/me`, one shortage and empty lists. */
function signInAs(orgType: string, capabilities: string[] = []) {
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
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/auth/me")) return Response.json(me);
      if (path === `/api/v1/shortages/${shortage.id}`) return Response.json(shortage);
      if (path.endsWith("/match-runs/latest"))
        return Response.json({ code: "not_found", message: "No run." }, { status: 404 });
      return Response.json(page([]));
    }),
  );
}

const navLabels = () => screen.getAllByRole("link").map((a) => a.textContent);

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
    signInAs(orgType, ALL_CAPS);
    render(<App />);
    expect(await screen.findByText(`Test ${orgType} org`)).toBeTruthy();
    expect(screen.getByText("Test User")).toBeTruthy();
    expect(screen.getByText(orgType)).toBeTruthy();
    for (const { label } of config.nav)
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
  });

  it("hides nav entries the user lacks the capability for", async () => {
    signInAs("HOSPITAL", ["receipt.record"]);
    render(<App />);
    await screen.findByText("Test HOSPITAL org");
    expect(navLabels()).toEqual(["Dashboard", "Inventory", "Deliveries", "Forecasts"]);
  });

  it("shows a requester Shortages but not Requests", async () => {
    signInAs("HOSPITAL", ["shortage.create"]);
    render(<App />);
    await screen.findByText("Test HOSPITAL org");
    expect(navLabels()).toContain("Shortages");
    expect(navLabels()).not.toContain("Requests");
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

  it("routes a shortage link to its detail screen", async () => {
    signInAs("HOSPITAL", ["shortage.create"]);
    window.history.replaceState(null, "", `/shortages/${shortage.id}`);
    render(<App />);
    expect(await screen.findByRole("link", { name: "← Shortages" })).toBeTruthy();
    expect(await screen.findByText("No match run yet")).toBeTruthy();
  });
});
