import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceRequest } from "../api";
import { incoming, meAs, NOW, page, products } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { RequestsPage } from "./requests";

// Fixture deadlines are relative to NOW; the countdown-expiry test also fakes the ticking.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const accepted: SourceRequest = {
  ...incoming,
  id: "5e000000-0000-4000-8000-0000000000a1",
  requester_org_name: "Hospital C",
  status: "TENTATIVE_HOLD",
  held_qty: 300,
  hold_expires_at: "2026-10-08T06:00:30Z",
};
const declined: SourceRequest = {
  ...incoming,
  id: "5e000000-0000-4000-8000-0000000000d1",
  requester_org_name: "Hospital E",
  status: "DECLINED",
  decline_reason: "Needed for our own theatre list",
  reason_source: "USER",
};

const superseded: SourceRequest = {
  ...incoming,
  id: "5e000000-0000-4000-8000-0000000000e1",
  requester_org_name: "Hospital F",
  status: "SUPERSEDED",
};

/** The fake hub's source-request list: `REQUESTED` for the awaiting list, everything else. */
const list =
  (awaiting: SourceRequest[], rest: SourceRequest[] = []) =>
  (call: Call) =>
    call.url.searchParams.get("status") === "REQUESTED"
      ? page(awaiting)
      : page([...awaiting, ...rest]);

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/source-requests": list([incoming], [accepted, declined]),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "STORE_MANAGER") =>
  renderAs(meAs(role), <RequestsPage />, { path: "/requests" });

const awaitingRow = async () => {
  const table = await screen.findByRole("table", { name: "Awaiting response" });
  return within(table).getAllByRole("row")[1]!;
};

/** Clicks `action` on the awaiting row, types `reason` if given, and confirms. */
async function respond(action: "Accept" | "Decline", reason?: string) {
  fireEvent.click(within(await awaitingRow()).getByRole("button", { name: action }));
  const dialog = screen.getByRole("dialog");
  if (reason)
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: reason },
    });
  fireEvent.click(
    within(dialog).getByRole("button", {
      name: action === "Accept" ? "Accept and hold" : "Decline",
    }),
  );
  return dialog;
}

describe("Incoming requests", () => {
  it("shows product, qty, requesting hospital and a live deadline countdown", async () => {
    const fake = hub();
    show();
    const row = await awaitingRow();
    expect(within(row).getByText("Surgical Kit A")).toBeTruthy();
    expect(within(row).getByText("SURG-KIT-A")).toBeTruthy();
    expect(within(row).getByText("300 kits")).toBeTruthy();
    expect(within(row).getByText("Hospital D")).toBeTruthy();
    expect(within(row).getByText("Routine")).toBeTruthy();
    expect(within(row).getByTestId("countdown").textContent).toBe("14:31 left");
    const queries = fake.to("GET", "/api/v1/source-requests").map((c) => c.url.searchParams);
    expect(queries.every((q) => q.get("direction") === "incoming")).toBe(true);
    expect(queries.map((q) => q.get("status"))).toContain("REQUESTED");
  });

  it("offers Accept and Decline to a store manager", async () => {
    hub();
    show("STORE_MANAGER");
    const row = await awaitingRow();
    expect(within(row).getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Decline" })).toBeTruthy();
  });

  it("shows no answer buttons without source_request.respond", async () => {
    hub();
    show("REQUESTER");
    const row = await awaitingRow();
    expect(within(row).getByText("Hospital D")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
  });

  it("takes the buttons away when the countdown runs out", async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(Date.parse(incoming.sla_deadline) - 2_000);
    hub();
    show();
    const row = await awaitingRow();
    expect(within(row).getByTestId("countdown").textContent).toBe("0:02 left");
    expect(within(row).getByRole("button", { name: "Accept" })).toBeTruthy();
    act(() => vi.advanceTimersByTime(2_000));
    expect(within(row).getByTestId("countdown").textContent).toBe("Deadline passed");
    expect(within(row).queryByRole("button", { name: "Accept" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Decline" })).toBeNull();
    expect(within(row).getByText("Too late to answer")).toBeTruthy();
  });

  it("accepts without a body when no reason is typed, then refetches", async () => {
    const fake = hub({
      "POST /api/v1/source-requests/{id}/accept": { ...incoming, status: "TENTATIVE_HOLD" },
    });
    show();
    await awaitingRow();
    const before = fake.to("GET", "/api/v1/source-requests").length;
    await respond("Accept");
    await waitFor(() =>
      expect(fake.to("POST", `/api/v1/source-requests/${incoming.id}/accept`)).toHaveLength(1),
    );
    expect(fake.to("POST", `/api/v1/source-requests/${incoming.id}/accept`)[0]!.body).toBe("");
    await waitFor(() =>
      expect(fake.to("GET", "/api/v1/source-requests").length).toBeGreaterThan(before),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("declines with the typed reason", async () => {
    const fake = hub({
      "POST /api/v1/source-requests/{id}/decline": { ...incoming, status: "DECLINED" },
    });
    show();
    await respond("Decline", "Needed for our own patients");
    await waitFor(() =>
      expect(fake.to("POST", `/api/v1/source-requests/${incoming.id}/decline`)).toHaveLength(1),
    );
    const [call] = fake.to("POST", `/api/v1/source-requests/${incoming.id}/decline`);
    expect(JSON.parse(call!.body!)).toEqual({ reason: "Needed for our own patients" });
  });

  it("shows the hub's message on a 409 and refetches the list", async () => {
    const message = "Stock changed before acceptance.";
    let answered = false;
    const fake = hub({
      "POST /api/v1/source-requests/{id}/accept": () => {
        answered = true;
        return hubError(409, "conflict", message);
      },
      // After the refusal the hub reports the request as EXPIRED.
      "GET /api/v1/source-requests": (call: Call) =>
        answered ? list([], [{ ...incoming, status: "EXPIRED" }])(call) : list([incoming])(call),
    });
    show();
    await respond("Accept");
    expect(await screen.findAllByText(message)).not.toHaveLength(0);
    expect(await screen.findByText("No requests awaiting your response")).toBeTruthy();
    const history = screen.getByRole("table", { name: "Answered and closed" });
    expect(within(history).getByText("Expired")).toBeTruthy();
    expect(fake.to("POST", `/api/v1/source-requests/${incoming.id}/accept`)).toHaveLength(1);
  });

  it("lists answered requests with their hold or decline reason", async () => {
    hub();
    show();
    const table = await screen.findByRole("table", { name: "Answered and closed" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const [held, refused] = rows;
    expect(within(held!).getByText("Hospital C")).toBeTruthy();
    expect(within(held!).getByText("Tentative hold")).toBeTruthy();
    expect(within(held!).getByText(/300 kits held/)).toBeTruthy();
    expect(within(held!).getByTestId("countdown").textContent).toBe("1 d 0 h left");
    expect(within(refused!).getByText("Declined")).toBeTruthy();
    expect(within(refused!).getByTestId("decline-reason").textContent).toBe(
      "Needed for our own theatre list",
    );
    expect(within(table).queryByRole("button")).toBeNull();
  });

  it("says a superseded request is no longer needed (S19)", async () => {
    hub({ "GET /api/v1/source-requests": list([], [superseded]) });
    show();
    const table = await screen.findByRole("table", { name: "Answered and closed" });
    const row = within(table).getByTestId(`request-${superseded.id}`);
    expect(within(row).getByText("Superseded")).toBeTruthy();
    expect(within(row).getByText("No longer needed")).toBeTruthy();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("shows loading, then empty states", async () => {
    hub({ "GET /api/v1/source-requests": page([]) });
    show();
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    expect(await screen.findByText("No requests awaiting your response")).toBeTruthy();
    expect(screen.getByText("No requests yet")).toBeTruthy();
  });

  it("shows the hub's message when the lists fail", async () => {
    hub({
      "GET /api/v1/source-requests": () => hubError(500, "internal_error", "Hub is down."),
    });
    show();
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    for (const alert of screen.getAllByRole("alert"))
      expect(alert.textContent).toContain("Hub is down.");
  });
});
