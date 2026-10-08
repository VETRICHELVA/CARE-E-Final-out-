// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { type Me, useAuth } from "@care-e/api-client";
import { can, useCan } from "./auth";

afterEach(cleanup);

const me = (capabilities: string[]): Me => ({
  user: {
    id: "u1",
    email: "requester@hospital-a.demo",
    full_name: "Hospital A Requester",
    org_id: "o1",
    is_active: true,
  },
  org: {
    id: "o1",
    name: "Hospital A",
    type: "HOSPITAL",
    status: "ACTIVE",
    location: { lat: 0, lng: 0 },
  },
  roles: [],
  capabilities,
});

function ApproveButton() {
  return useCan("recommendation.approve") ? <button>Approve transfer</button> : null;
}

/** Renders with `/auth/me` already in the cache, as after sign-in. */
function renderAs(user: Me) {
  useAuth.setState({ tokens: { access: "a", refresh: "r" } });
  const queryClient = new QueryClient();
  queryClient.setQueryData(["/api/v1/auth/me"], user);
  render(
    <QueryClientProvider client={queryClient}>
      <ApproveButton />
    </QueryClientProvider>,
  );
}

describe("can()", () => {
  it("is true only for a capability the user has", () => {
    expect(can(me(["recommendation.approve"]), "recommendation.approve")).toBe(true);
    expect(can(me(["shortage.create"]), "recommendation.approve")).toBe(false);
    expect(can(undefined, "recommendation.approve")).toBe(false);
  });

  it("accepts any one of a list of capabilities", () => {
    const readers = ["shortage.create", "recommendation.approve"];
    expect(can(me(["recommendation.approve"]), readers)).toBe(true);
    expect(can(me(["receipt.record"]), readers)).toBe(false);
    expect(can(undefined, readers)).toBe(false);
  });

  it("hides the action from a user without the capability", () => {
    renderAs(me(["shortage.create"]));
    expect(screen.queryByRole("button", { name: "Approve transfer" })).toBeNull();
  });

  it("shows the action to a user with the capability", () => {
    renderAs(me(["recommendation.approve"]));
    expect(screen.getByRole("button", { name: "Approve transfer" })).toBeTruthy();
  });
});
