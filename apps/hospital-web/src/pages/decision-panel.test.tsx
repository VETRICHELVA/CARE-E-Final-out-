import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@care-e/api-client";
import type { Recommendation } from "../api";
import {
  buyRec,
  matchRun,
  meAs,
  NOW,
  page,
  products,
  shortage,
  splitRec,
  transferRec,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { ShortageDetailPage } from "./shortage-detail";

// The recommendation fixtures are valid until 06:30:01; NOW is 06:00:30.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const recPath = (rec: Recommendation, action = "") =>
  `/api/v1/recommendations/${rec.id}${action ? `/${action}` : ""}`;

/** business-rules.md §13, copied here on purpose: the test pins the exact wording. */
const WORDING = {
  TRANSFER: ["Approve transfer", "Stock is now held at the source."],
  TRANSFER_SPLIT: ["Approve transfers", "Stock is now held at each source."],
  BUY: ["Approve purchase", "The order has gone to the supplier."],
} as const;

const latestPath = `/api/v1/shortages/${shortage.id}/recommendations/latest`;

/** A fake hub whose recommendation `rec` changes as the decisions arrive. */
function hub(rec: Recommendation, extra: Record<string, unknown> = {}) {
  let current = rec;
  const decided = (to: Recommendation["status"], call: Call): Recommendation => {
    const typed = call.body ? (JSON.parse(call.body) as { reason?: string }).reason : undefined;
    current = {
      ...rec,
      status: to,
      decided_at: "2026-10-07T06:01:00Z",
      decided_by: "00000000-0000-4000-8000-0000000000aa",
      reason: typed ?? null,
      reason_source: typed ? "USER" : "SYSTEM",
    };
    return current;
  };
  return fakeHub({
    "GET /api/v1/shortages/{id}": { ...shortage, status: "AWAITING_DECISION" },
    "GET /api/v1/shortages/{id}/match-runs/latest": matchRun,
    "GET /api/v1/products": products,
    "GET /api/v1/audit": page([]),
    "GET /api/v1/source-requests": page([]),
    "GET /api/v1/shortages/{id}/recommendations/latest": () => current,
    "GET /api/v1/recommendations/{id}": () => current,
    "POST /api/v1/recommendations/{id}/approve": (call: Call) => ({
      recommendation: decided("APPROVED", call),
      message: WORDING[rec.type][1],
      shipment_ids: [],
      purchase_order_id: null,
    }),
    "POST /api/v1/recommendations/{id}/reject": (call: Call) => decided("REJECTED", call),
    "POST /api/v1/recommendations/{id}/escalate": (call: Call) => decided("ESCALATED", call),
    ...extra,
  });
}

const show = (me: Me = meAs("APPROVER")) =>
  renderAs(me, <ShortageDetailPage />, {
    path: `/shortages/${shortage.id}`,
    route: "/shortages/:id",
  });

const panel = () => screen.findByTestId("decision-panel");

/** Opens `button`'s dialog, types `reason` if given, and confirms with `confirm`. */
async function decide(button: string, reason?: string, confirm = button) {
  fireEvent.click(within(await panel()).getByRole("button", { name: button }));
  const dialog = screen.getByRole("dialog");
  if (reason)
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: reason },
    });
  fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
  return dialog;
}

describe("Decision panel", () => {
  it.each([
    ["TRANSFER", transferRec, "Transfer"],
    ["TRANSFER_SPLIT", splitRec, "Split transfer"],
    ["BUY", buyRec, "Buy"],
  ] as const)(
    "words the %s button and the post-approval message exactly as business-rules §13",
    async (type, rec, badge) => {
      const fake = hub(rec);
      show();
      const box = await panel();
      expect(within(box).getByTestId("recommendation-type").textContent).toBe(badge);
      const [button, message] = WORDING[type];
      const others = Object.values(WORDING)
        .map(([b]) => b)
        .filter((b) => b !== button);
      expect(within(box).getByRole("button", { name: button })).toBeTruthy();
      for (const other of others)
        expect(within(box).queryByRole("button", { name: other })).toBeNull();

      await decide(button);
      await waitFor(() => expect(fake.to("POST", recPath(rec, "approve"))).toHaveLength(1));
      expect(fake.to("POST", recPath(rec, "approve"))[0]!.body).toBe("");
      expect((await screen.findByTestId("approved-message")).textContent).toBe(message);
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(within(await panel()).queryByRole("button", { name: button })).toBeNull();
    },
  );

  it.each([
    ["TRANSFER", transferRec],
    ["TRANSFER_SPLIT", splitRec],
    ["BUY", buyRec],
  ] as const)("shows §13's message for an already approved %s", async (type, rec) => {
    hub({
      ...rec,
      status: "APPROVED",
      decided_at: "2026-10-07T06:01:00Z",
      reason_source: "SYSTEM",
    });
    show();
    expect((await screen.findByTestId("approved-message")).textContent).toBe(WORDING[type][1]);
    expect(within(await panel()).queryByTestId("decision-actions")).toBeNull();
  });

  it("reads the shortage's latest recommendation from the hub, not the audit trail", async () => {
    const fake = hub(transferRec);
    show();
    await panel();
    expect(fake.to("GET", latestPath)).toHaveLength(1);
    expect(
      fake
        .to("GET", "/api/v1/audit")
        .filter((c) => c.url.searchParams.get("entity") === "recommendation"),
    ).toHaveLength(0);
  });

  it("shows no panel before the hub has made a recommendation", async () => {
    const fake = hub(transferRec, {
      "GET /api/v1/shortages/{id}": { ...shortage, status: "MATCHING" },
      "GET /api/v1/shortages/{id}/recommendations/latest": () =>
        hubError(404, "not_found", "No recommendation yet."),
    });
    show();
    expect(await screen.findByTestId("shortfall")).toBeTruthy();
    await waitFor(() => expect(fake.to("GET", latestPath)).toHaveLength(1));
    expect(screen.queryByTestId("decision-panel")).toBeNull();
    expect(screen.queryByText("No recommendation yet.")).toBeNull();
    expect(fake.to("GET", recPath(transferRec))).toHaveLength(0);
  });

  it("says so if a shortage awaiting a decision has no recommendation", async () => {
    hub(transferRec, {
      "GET /api/v1/shortages/{id}/recommendations/latest": () =>
        hubError(404, "not_found", "No recommendation yet."),
    });
    show();
    expect(await screen.findByText("The recommendation could not be found")).toBeTruthy();
  });

  it("shows lines, ETA, explanation and alternatives, with '—' for a hospital's cost", async () => {
    hub(transferRec);
    show();
    const box = await panel();
    expect(within(box).getByTestId("explanation").textContent).toBe(transferRec.explanation);
    const lines = within(box).getByRole("table", { name: "Recommended sources" });
    const [b] = within(lines).getAllByRole("row").slice(1);
    expect(within(b!).getByText("Hospital B")).toBeTruthy();
    expect(within(b!).getByText("Hospital")).toBeTruthy();
    expect(within(b!).getByText("850 kits")).toBeTruthy();
    expect(within(b!).getByText("6.2 h")).toBeTruthy();
    expect(within(b!).getByText("180 days")).toBeTruthy();
    // Unit price and landed cost are null for a hospital source: shown as "—", never computed.
    expect(
      within(b!)
        .getAllByLabelText("Not shown")
        .map((c) => c.textContent),
    ).toEqual(["—", "—"]);
    expect(b!.textContent).not.toContain("₹");
    expect(within(box).getByTestId("total-cost").textContent).toBe(
      "Total landed cost: — (not shown when a hospital source is involved)",
    );
    const alternatives = within(box).getByRole("table", { name: "Alternatives" });
    const [y] = within(alternatives).getAllByRole("row").slice(1);
    expect(within(y!).getByText("Supplier Y")).toBeTruthy();
    expect(within(y!).getByText("24 h")).toBeTruthy();
    expect(within(y!).getByText("₹28.00")).toBeTruthy();
    expect(within(y!).getByText("₹24,000.00")).toBeTruthy();
  });

  it("shows the hub's total for a BUY and says when there is no alternative", async () => {
    hub({ ...buyRec, alternatives: [] });
    show();
    const box = await panel();
    expect(within(box).getByTestId("total-cost").textContent).toBe("Total landed cost: ₹24,000.00");
    expect(within(box).getByText("No alternative.")).toBeTruthy();
  });

  it("counts down the validity and takes the buttons away when it passes", async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(Date.parse(transferRec.expires_at) - 2_000);
    hub(transferRec);
    show();
    const box = await panel();
    expect(within(box).getByTestId("countdown").textContent).toBe("0:02 left");
    expect(within(box).getByRole("button", { name: "Approve transfer" })).toBeTruthy();
    act(() => vi.advanceTimersByTime(2_000));
    expect(within(box).getByTestId("countdown").textContent).toBe("Validity passed");
    expect(within(box).queryByTestId("decision-actions")).toBeNull();
    expect(within(box).getByTestId("validity-passed")).toBeTruthy();
  });

  it("shows the full countdown while the recommendation is valid", async () => {
    hub(transferRec);
    show();
    expect(within(await panel()).getByTestId("countdown").textContent).toBe("29:31 left");
  });

  it("shows a requester without audit.read the recommendation, but no decision", async () => {
    const fake = hub(transferRec);
    show(meAs("REQUESTER"));
    const box = await panel();
    expect(within(box).getByTestId("recommendation-type").textContent).toBe("Transfer");
    expect(within(box).queryByTestId("decision-actions")).toBeNull();
    expect(within(box).queryByRole("button", { name: "Approve transfer" })).toBeNull();
    expect(
      within(box).getByText("Waiting for an approver in your organization to decide."),
    ).toBeTruthy();
    expect(fake.to("GET", "/api/v1/audit")).toHaveLength(0);
  });

  it("offers Approve, Escalate and Reject while PENDING", async () => {
    hub(transferRec);
    show();
    const actions = within(await panel()).getByTestId("decision-actions");
    expect(
      within(actions)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Approve transfer", "Escalate", "Reject"]);
  });

  it("offers Approve and Reject but not Escalate once ESCALATED, with the reason given", async () => {
    hub({
      ...transferRec,
      status: "ESCALATED",
      reason: "Over my approval limit",
      reason_source: "USER",
    });
    show();
    const box = await panel();
    const actions = within(box).getByTestId("decision-actions");
    expect(
      within(actions)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Approve transfer", "Reject"]);
    const reason = within(box).getByTestId("recorded-reason");
    expect(reason.dataset.reasonSource).toBe("USER");
    expect(reason.textContent).toBe("User reasonOver my approval limit");
  });

  it.each(["REJECTED", "EXPIRED"] as const)("offers no decision once %s", async (status) => {
    hub({ ...transferRec, status, reason: "Validity passed.", reason_source: "SYSTEM" });
    show();
    const box = await panel();
    expect(within(box).queryByTestId("decision-actions")).toBeNull();
    expect(within(box).queryByTestId("countdown")).toBeNull();
    expect(within(box).getByTestId("recorded-reason").dataset.reasonSource).toBe("SYSTEM");
  });

  it("rejects with the typed reason", async () => {
    const fake = hub(transferRec);
    show();
    await decide("Reject", "Supplier is cheaper this week");
    await waitFor(() => expect(fake.to("POST", recPath(transferRec, "reject"))).toHaveLength(1));
    expect(JSON.parse(fake.to("POST", recPath(transferRec, "reject"))[0]!.body!)).toEqual({
      reason: "Supplier is cheaper this week",
    });
    await waitFor(() =>
      expect(within(screen.getByTestId("decision-panel")).getByText("Rejected")).toBeTruthy(),
    );
    const reason = within(await panel()).getByTestId("recorded-reason");
    expect(reason.textContent).toBe("User reasonSupplier is cheaper this week");
  });

  it("escalates without a body when no reason is typed", async () => {
    const fake = hub(transferRec);
    show();
    await decide("Escalate");
    await waitFor(() => expect(fake.to("POST", recPath(transferRec, "escalate"))).toHaveLength(1));
    expect(fake.to("POST", recPath(transferRec, "escalate"))[0]!.body).toBe("");
    const reason = await within(await panel()).findByTestId("recorded-reason");
    expect(reason.dataset.reasonSource).toBe("SYSTEM");
    expect(reason.textContent).toBe("SystemNo reason was entered.");
  });

  it("shows the hub's message when a decision is refused, and refetches", async () => {
    const fake = hub(transferRec, {
      "POST /api/v1/recommendations/{id}/approve": hubError(
        409,
        "invalid_transition",
        "This recommendation has expired. Matching has run again.",
      ),
    });
    show();
    await panel();
    const before = fake.to("GET", recPath(transferRec)).length;
    const dialog = await decide("Approve transfer");
    expect(
      await within(dialog).findByText("This recommendation has expired. Matching has run again."),
    ).toBeTruthy();
    await waitFor(() =>
      expect(fake.to("GET", recPath(transferRec)).length).toBeGreaterThan(before),
    );
    expect(screen.queryByTestId("approved-message")).toBeNull();
  });
});
