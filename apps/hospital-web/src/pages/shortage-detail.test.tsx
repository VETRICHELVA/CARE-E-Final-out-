import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Shortage } from "../api";
import { auditRows, matchRun, meAs, page, products, shortage } from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { ShortageDetailPage } from "./shortage-detail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const base = `/api/v1/shortages/${shortage.id}`;

const hub = (extra: Record<string, unknown> = {}, s: Partial<Shortage> = {}) =>
  fakeHub({
    "GET /api/v1/shortages/{id}": { ...shortage, ...s },
    "GET /api/v1/shortages/{id}/match-runs/latest": matchRun,
    "GET /api/v1/products": products,
    "GET /api/v1/audit": page(auditRows),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "REQUESTER") =>
  renderAs(meAs(role), <ShortageDetailPage />, {
    path: `/shortages/${shortage.id}`,
    route: "/shortages/:id",
  });

describe("Shortage detail", () => {
  it("shows the shortage's status and the hub's shortfall", async () => {
    hub();
    show();
    expect((await screen.findByTestId("shortfall")).textContent).toBe("850 kits");
    expect(screen.getAllByText("Matching").length).toBeGreaterThan(0);
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.getByText("1,000 kits")).toBeTruthy();
  });

  it("ranks eligible candidates in the hub's order with qty, ETA and landed cost", async () => {
    hub();
    show();
    const table = await screen.findByRole("table", { name: "Eligible sources" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => (r as HTMLTableRowElement).cells[0]?.textContent)).toEqual([
      "1",
      "2",
      "3",
    ]);
    expect(rows.map((r) => within(r).getByText(/^(Hospital|Supplier) [A-Z]$/).textContent)).toEqual(
      ["Hospital B", "Supplier Y", "Supplier X"],
    );
    const [b, y] = rows;
    expect(within(b!).getByText("1,000 kits transferable")).toBeTruthy();
    expect(within(b!).getByText("6.2 h")).toBeTruthy();
    // A hospital source's cost is hidden by the hub (landed_cost_paise: null).
    expect(within(b!).getByLabelText("Not shown").textContent).toBe("—");
    expect(within(y!).getByText("2,000 kits offered")).toBeTruthy();
    expect(within(y!).getByText("24 h")).toBeTruthy();
    expect(within(y!).getByText("₹24,000.00")).toBeTruthy();
    expect(screen.getByTestId("plan").textContent).toBe(
      "Planned: Transfer — 850 kits from Hospital B",
    );
  });

  it("groups rejected candidates with the hub's exact reason text", async () => {
    hub();
    show();
    const list = await screen.findByRole("list", { name: "Rejected sources" });
    expect(screen.getByText("Not eligible (3)")).toBeTruthy();
    const items = within(list)
      .getAllByRole("listitem")
      .filter((li) => li.parentElement === list);
    const byName = Object.fromEntries(
      items.map((li) => [
        li.querySelector(".font-medium")?.textContent,
        within(li)
          .getAllByTestId("reason")
          .map((r) => r.textContent),
      ]),
    );
    expect(byName).toEqual({
      "Hospital C": ["Only 100 transferable; 850 needed"],
      "Hospital D": ["Expires in 12 days; 30 required"],
      "Hospital E": ["Not authorized to supply this product"],
    });
    expect(within(list).getByText("Quantity:")).toBeTruthy();
    expect(within(list).getByText("Shelf life:")).toBeTruthy();
    expect(within(list).getByText("Authorization:")).toBeTruthy();
  });

  it("says so when the shortage has no match run yet", async () => {
    hub({
      "GET /api/v1/shortages/{id}/match-runs/latest": hubError(
        404,
        "not_found",
        "This shortage has no match run yet.",
      ),
    });
    show();
    expect(await screen.findByText("No match run yet")).toBeTruthy();
  });

  it("shows the hub's reason when nothing is eligible", async () => {
    hub({
      "GET /api/v1/shortages/{id}/match-runs/latest": {
        ...matchRun,
        planned_resolution: null,
        reason: "No eligible source",
        candidates: matchRun.candidates.filter((c) => !c.eligible),
      },
    });
    show();
    expect((await screen.findByTestId("plan")).textContent).toBe("No eligible source");
    expect(screen.getByText("Eligible (0)")).toBeTruthy();
  });

  it("shows the hub's error when the shortage can't be read", async () => {
    hub({
      "GET /api/v1/shortages/{id}": hubError(
        403,
        "forbidden",
        "This shortage belongs to another organization.",
      ),
    });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "This shortage belongs to another organization.",
    );
  });

  it("offers Re-run match and Cancel to a requester while the shortage is MATCHING", async () => {
    hub();
    show("REQUESTER");
    expect(await screen.findByRole("button", { name: "Re-run match" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel shortage" })).toBeTruthy();
  });

  it("hides both actions from a user without shortage.create", async () => {
    hub();
    show("APPROVER");
    await screen.findByTestId("shortfall");
    expect(screen.queryByRole("button", { name: "Re-run match" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel shortage" })).toBeNull();
  });

  it("gates actions by the shortage's state", async () => {
    hub({}, { status: "AWAITING_DECISION" });
    show();
    expect(await screen.findByRole("button", { name: "Cancel shortage" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Re-run match" })).toBeNull();
    cleanup();

    hub({}, { status: "RESOLVED" });
    show();
    await screen.findByTestId("shortfall");
    expect(screen.queryByRole("button", { name: "Cancel shortage" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Re-run match" })).toBeNull();
  });

  it("re-runs the match with the typed reason and refreshes the run", async () => {
    let runs = 0;
    const fake = hub({
      "POST /api/v1/shortages/{id}/match": () => ({
        ...matchRun,
        run_no: 2,
        triggered_by: "MANUAL",
      }),
      "GET /api/v1/shortages/{id}/match-runs/latest": () =>
        runs++ === 0 ? matchRun : { ...matchRun, run_no: 2, triggered_by: "MANUAL" },
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Re-run match" }));
    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "Stock arrived at B" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Re-run match" }),
    );
    expect(await screen.findByText("Latest match run #2")).toBeTruthy();
    const [call] = fake.to("POST", `${base}/match`);
    expect(JSON.parse(call!.body!)).toEqual({ reason: "Stock arrived at B" });
  });

  it("cancels without a body when no reason is typed", async () => {
    const fake = hub({
      "POST /api/v1/shortages/{id}/cancel": { ...shortage, status: "CANCELLED" },
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel shortage" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel shortage" }),
    );
    await waitFor(() => expect(fake.to("POST", `${base}/cancel`)).toHaveLength(1));
    expect(fake.to("POST", `${base}/cancel`)[0]!.body).toBe("");
  });

  it("shows the hub's message when a cancel is refused", async () => {
    const message = "Cannot move a shortage from IN_FULFILLMENT to CANCELLED.";
    hub({ "POST /api/v1/shortages/{id}/cancel": hubError(409, "invalid_transition", message) });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel shortage" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel shortage" }),
    );
    expect((await within(screen.getByRole("dialog")).findByRole("alert")).textContent).toBe(
      message,
    );
  });

  it("builds the timeline from the audit trail for a user with audit.read", async () => {
    const fake = hub();
    show("APPROVER");
    const timeline = await screen.findByRole("list", { name: "Status timeline" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector(".font-medium")?.textContent)).toEqual([
      "Reported",
      "Open → Matching",
      "Match run #1",
    ]);
    expect(within(timeline).getAllByText(/No reason was entered\./)).toHaveLength(2);
    expect(fake.to("GET", "/api/v1/audit")[0]!.url.searchParams.get("entity_id")).toBe(shortage.id);
  });

  it("builds the timeline from the shortage itself without audit.read", async () => {
    const fake = hub();
    show("REQUESTER");
    const timeline = await screen.findByRole("list", { name: "Status timeline" });
    const titles = within(timeline)
      .getAllByRole("listitem")
      .map((li) => li.querySelector(".font-medium")?.textContent);
    expect(titles).toEqual(["Reported", "Status: Matching", "Match run #1"]);
    expect(fake.to("GET", "/api/v1/audit")).toHaveLength(0);
  });
});
