import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuditRow } from "../api";
import {
  auditRow,
  buyRec,
  matchRun,
  ME_ID,
  meAs,
  NOW,
  ORG_Y,
  page,
  PO_ID,
  products,
  RECEIPT_ID,
  recCreated,
  requestToB,
  shipmentToA,
  shortage,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { ShortageDetailPage } from "./shortage-detail";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const status = (from: string | null, to: string) => ({
  before: from ? { status: from } : null,
  after: { status: to },
});

// Scenario 1 as Hospital A's own trail records it (business-rules.md §10).
const ROWS: Record<string, AuditRow[]> = {
  [shortage.id]: [
    auditRow({
      id: "ad000000-0000-4000-8000-000000000101",
      actor_id: ME_ID,
      action: "shortage.created",
      ...status(null, "OPEN"),
      reason: "Theatre list moved up",
      reason_source: "USER",
      ts: "2026-10-07T06:00:00Z",
    }),
  ],
  [requestToB.id]: [
    auditRow({
      id: "ad000000-0000-4000-8000-000000000102",
      entity: "source_request",
      entity_id: requestToB.id,
      action: "source_request.status_changed",
      ...status("REQUESTED", "DECLINED"),
      reason: "All our stock is booked for surgery",
      reason_source: "USER",
      ts: "2026-10-07T06:02:00Z",
    }),
    auditRow({
      id: "ad000000-0000-4000-8000-000000000103",
      entity: "source_request",
      entity_id: requestToB.id,
      action: "source_request.status_changed",
      ...status("REQUESTED", "EXPIRED"),
      reason: "Response deadline passed.",
      reason_source: "SYSTEM",
      ts: "2026-10-07T06:01:00Z",
    }),
  ],
  [RECEIPT_ID]: [
    auditRow({
      id: "ad000000-0000-4000-8000-000000000108",
      actor_id: ME_ID,
      entity: "receipt",
      entity_id: RECEIPT_ID,
      action: "receipt.recorded",
      after: { shipment_id: shipmentToA.id, received: 850, accepted: 790, rejected: 60 },
      reason: "60 kits had torn seals",
      reason_source: "USER",
      ts: "2026-10-07T09:00:00Z",
    }),
    auditRow({
      id: "ad000000-0000-4000-8000-000000000109",
      entity: "reconciliation",
      entity_id: "ae000000-0000-4000-8000-000000000001",
      action: "reconciliation.completed",
      after: { outcome: "PARTIAL" },
      reason: "790 of the 850 needed were accepted; 60 are still needed.",
      reason_source: "SYSTEM",
      ts: "2026-10-07T09:00:01Z",
    }),
  ],
  [buyRec.id]: [recCreated(buyRec)],
  [PO_ID]: [
    auditRow({
      id: "ad000000-0000-4000-8000-000000000104",
      actor_id: ME_ID,
      entity: "purchase_order",
      entity_id: PO_ID,
      action: "purchase_order.created",
      ...status(null, "SENT"),
      after: { status: "SENT", shortage_id: shortage.id, supplier_org_id: ORG_Y },
      ts: "2026-10-07T06:03:00Z",
    }),
    auditRow({
      id: "ad000000-0000-4000-8000-000000000105",
      entity: "purchase_order",
      entity_id: PO_ID,
      action: "purchase_order.status_changed",
      ...status("SENT", "ACKNOWLEDGED"),
      ts: "2026-10-07T06:04:00Z",
    }),
  ],
  [shipmentToA.id]: [
    auditRow({
      id: "ad000000-0000-4000-8000-000000000106",
      entity: "shipment",
      entity_id: shipmentToA.id,
      action: "shipment.created",
      before: null,
      after: { status: "CREATED", shortage_id: shortage.id, from_org_id: ORG_Y },
      reason: "Dispatched from the north warehouse",
      reason_source: "USER",
      ts: "2026-10-07T06:05:00Z",
    }),
    auditRow({
      id: "ad000000-0000-4000-8000-000000000107",
      entity: "shipment",
      entity_id: shipmentToA.id,
      action: "shipment.status_changed",
      ...status("ASSIGNED", "PICKED_UP"),
      ts: "2026-10-07T06:06:00Z",
    }),
  ],
};

/** `GET /shortages/{id}/audit`: the whole trail in this org, oldest first, as the hub pages it. */
const TRAIL = Object.values(ROWS)
  .flat()
  .sort((a, b) => a.ts.localeCompare(b.ts));
const trailPath = `/api/v1/shortages/${shortage.id}/audit`;

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/shortages/{id}": { ...shortage, status: "IN_FULFILLMENT" },
    "GET /api/v1/shortages/{id}/match-runs/latest": matchRun,
    "GET /api/v1/products": products,
    "GET /api/v1/shortages/{id}/audit": page(TRAIL),
    "GET /api/v1/shortages/{id}/recommendations/latest": { ...buyRec, status: "APPROVED" },
    // The status timeline's own read of the shortage's rows.
    "GET /api/v1/audit": () => page(ROWS[shortage.id]!),
    "GET /api/v1/source-requests": page([
      { ...requestToB, status: "DECLINED", reason_source: "USER" },
    ]),
    "GET /api/v1/recommendations/{id}": { ...buyRec, status: "APPROVED" },
    "GET /api/v1/shipments/{id}": shipmentToA,
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "APPROVER") =>
  renderAs(meAs(role), <ShortageDetailPage />, {
    path: `/shortages/${shortage.id}`,
    route: "/shortages/:id",
  });

async function openAudit() {
  // Radix tabs switch on mouse down.
  fireEvent.mouseDown(await screen.findByRole("tab", { name: "Audit trail" }), { button: 0 });
  const list = await screen.findByRole("list", { name: "Audit trail" });
  return within(list).getAllByTestId("audit-row");
}

/** The audit row whose description is `action`. */
const rowFor = (rows: HTMLElement[], action: string) => {
  const row = rows.find((r) => within(r).getByTestId("audit-action").textContent === action);
  if (!row) throw new Error(`No audit row "${action}"`);
  return row;
};
const actor = (row: HTMLElement) => within(row).getByTestId("audit-actor").textContent;
const reason = (row: HTMLElement) => within(row).getByTestId("recorded-reason");

describe("Audit tab", () => {
  it("shows the shortage's and its records' rows from the hub's trail, newest first", async () => {
    const fake = hub();
    show();
    await openAudit();
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(10));
    expect(screen.getAllByTestId("audit-action").map((a) => a.textContent)).toEqual([
      "Reconciliation completed",
      "Receipt recorded",
      "Shipment: Assigned → Picked up",
      "Shipment created (Created)",
      "Purchase order: Sent → Acknowledged",
      "Purchase order created (Sent)",
      "Source request: Requested → Declined",
      "Source request: Requested → Expired",
      "Recommendation created (Pending)",
      "Shortage created (Open)",
    ]);
    expect(fake.to("GET", trailPath)).toHaveLength(1);
    // No per-record lookups: the hub's trail covers every record of the shortage.
    expect(
      fake
        .to("GET", "/api/v1/audit")
        .filter((c) => c.url.searchParams.get("entity") !== "shortage"),
    ).toHaveLength(0);
  });

  it("reads every page of a long trail", async () => {
    const fake = hub({
      "GET /api/v1/shortages/{id}/audit": (call: Call) =>
        call.url.searchParams.get("cursor") === "p2"
          ? page(TRAIL.slice(5))
          : { items: TRAIL.slice(0, 5), next_cursor: "p2" },
    });
    show();
    await openAudit();
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(10));
    expect(fake.to("GET", trailPath)).toHaveLength(2);
    expect(screen.getAllByTestId("audit-action")[0]!.textContent).toBe("Reconciliation completed");
  });

  it("labels USER and SYSTEM reasons differently", async () => {
    hub();
    show();
    await openAudit();
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(10));
    const rows = screen.getAllByTestId("audit-row");

    const typed = reason(rowFor(rows, "Source request: Requested → Declined"));
    expect(typed.dataset.reasonSource).toBe("USER");
    expect(within(typed).getByText("User reason")).toBeTruthy();
    expect(within(typed).getByText("All our stock is booked for surgery")).toBeTruthy();

    const timer = reason(rowFor(rows, "Source request: Requested → Expired"));
    expect(timer.dataset.reasonSource).toBe("SYSTEM");
    expect(within(timer).getByText("System")).toBeTruthy();
    expect(within(timer).getByText("Response deadline passed.")).toBeTruthy();

    const receipt = reason(rowFor(rows, "Receipt recorded"));
    expect(receipt.dataset.reasonSource).toBe("USER");
    expect(receipt.textContent).toBe("User reason60 kits had torn seals");
    const reconciled = reason(rowFor(rows, "Reconciliation completed"));
    expect(reconciled.dataset.reasonSource).toBe("SYSTEM");
    expect(within(reconciled).getByText("System")).toBeTruthy();

    const untyped = reason(rowFor(rows, "Purchase order created (Sent)"));
    expect(untyped.dataset.reasonSource).toBe("SYSTEM");
    expect(untyped.textContent).toBe("SystemNo reason was entered.");
    expect(within(typed).queryByText("System")).toBeNull();
  });

  it("shows a mirrored row as the other org's action, never as System", async () => {
    hub();
    show();
    await openAudit();
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(10));
    const rows = screen.getAllByTestId("audit-row");
    // B's decline (typed reason), mirrored into A's trail with actor_id null.
    expect(actor(rowFor(rows, "Source request: Requested → Declined"))).toBe(
      "Hospital B Other organization",
    );
    // The supplier's acknowledgement and dispatch, without a reason and with one.
    expect(actor(rowFor(rows, "Purchase order: Sent → Acknowledged"))).toBe(
      "Supplier Y Other organization",
    );
    expect(actor(rowFor(rows, "Shipment created (Created)"))).toBe("Supplier Y Other organization");
    // The carrier's driver picking up.
    await waitFor(() =>
      expect(
        actor(rowFor(screen.getAllByTestId("audit-row"), "Shipment: Assigned → Picked up")),
      ).toBe("SwiftMed Logistics Other organization"),
    );
  });

  it("shows the hub's own changes as System and this user's as You", async () => {
    hub();
    show();
    await openAudit();
    await waitFor(() => expect(screen.getAllByTestId("audit-row")).toHaveLength(10));
    const rows = screen.getAllByTestId("audit-row");
    expect(actor(rowFor(rows, "Source request: Requested → Expired"))).toBe("System");
    expect(actor(rowFor(rows, "Recommendation created (Pending)"))).toBe("System");
    expect(actor(rowFor(rows, "Shortage created (Open)"))).toBe("You");
    expect(actor(rowFor(rows, "Purchase order created (Sent)"))).toBe("You");
    expect(actor(rowFor(rows, "Receipt recorded"))).toBe("You");
    expect(actor(rowFor(rows, "Reconciliation completed"))).toBe("System");
  });

  it("is not offered without audit.read", async () => {
    hub();
    show("REQUESTER");
    expect(await screen.findByTestId("shortfall")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Audit trail" })).toBeNull();
  });

  it("shows the hub's error", async () => {
    hub({
      "GET /api/v1/shortages/{id}/audit": () =>
        hubError(500, "internal_error", "Audit is unavailable."),
    });
    show();
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Audit trail" }), { button: 0 });
    expect((await screen.findAllByText("Audit is unavailable.")).length).toBeGreaterThan(0);
  });

  it("has an empty state", async () => {
    hub({
      "GET /api/v1/shortages/{id}/audit": page([]),
      "GET /api/v1/audit": page([]),
      "GET /api/v1/source-requests": page([]),
    });
    show();
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Audit trail" }), { button: 0 });
    expect(await screen.findByText("No audit rows yet")).toBeTruthy();
  });
});
