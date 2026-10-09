import { expect, type Page, test } from "@playwright/test";
import { APP, assign, confirm, drive, recordReceipt, reportShortage, signIn } from "./support/apps";

// Scenario 1 (docs/specs/demo-scenarios.md), the whole loop, from the full demo seed: Hospital A
// reports a critical Surgical Kit A shortage; matching asks Hospital B and rejects C, D and E
// with the hub's reasons; B declines without a reason; the hub recommends buying from Supplier Y
// (X the alternative); A's approver approves; Supplier Y acknowledges and dispatches
// (supplier-web); SwiftMed assigns Ravi, who picks up and delivers (delivery-web); A's receiver
// records 790 of 850; the shortage is PARTIALLY_RESOLVED and a residual of 60 opens and matches;
// the audit trail shows B's decline as SYSTEM, "No reason was entered."
//
// Data: `make e2e` first runs e2e/seed/scenarios.py (the demo seed's numbers back, Hospital A's
// open Surgical Kit A shortages cancelled). Running Playwright directly? Run it first:
//   cd services/hub-api && uv run python ../../e2e/seed/scenarios.py
// Live updates need the hub's event publisher (`make worker`).

/** The rejected candidate's failed-gate reasons, as the page shows them. */
const reasonsOf = (page: Page, source: string) =>
  page
    .getByRole("list", { name: "Rejected sources" })
    .locator(":scope > li")
    .filter({ hasText: source })
    .getByTestId("reason");

test("Scenario 1: critical shortage, B declines, BUY from Y, 790 of 850 → residual 60", async ({
  browser,
}) => {
  test.setTimeout(240_000);

  // Step 1: Hospital A reports the shortage; the hub computes the shortfall.
  const a = await signIn(browser, "hospital", "store.manager@hospital-a.demo");
  const shortageId = await reportShortage(
    a,
    {
      product: "Surgical Kit A (SURG-KIT-A)",
      required: 1000,
      usable: 150,
      hours: 72,
      priority: "CRITICAL",
      minShelfLifeDays: 30,
    },
    "850 kit",
  );

  // Step 2: B is eligible and asked (15-minute deadline); C, D and E are rejected.
  await expect(a.getByText("Latest match run #1")).toBeVisible();
  await expect(a.getByTestId("plan")).toHaveText("Planned: Transfer — 850 kit from Hospital B");
  const eligible = a.getByRole("table", { name: "Eligible sources" });
  await expect(eligible.getByRole("row").nth(1)).toContainText("Hospital B");
  await expect(eligible).toContainText("1,000 kit transferable");
  await expect(reasonsOf(a, "Hospital C")).toHaveText(["Only 100 transferable; 850 needed"]);
  // The seed pins D's expiry so the delivery-day count reads 12 (app/seed.py, pinned_expiry).
  await expect(reasonsOf(a, "Hospital D")).toHaveText(["Expires in 12 days; 30 required"]);
  await expect(reasonsOf(a, "Hospital E")).toHaveText(["Not authorized to supply this product"]);
  const requests = a.getByRole("table", { name: "Source requests" });
  const toB = requests.getByRole("row").filter({ hasText: "Hospital B" });
  await expect(toB).toContainText("Requested");
  await expect(toB.getByTestId("countdown")).toHaveText(/^1[45]:\d\d left$/);
  await expect(a.getByRole("button", { name: "Re-run match" })).toHaveCount(0);

  // Step 3: Hospital B declines, without a reason.
  const b = await signIn(browser, "hospital", "store.manager@hospital-b.demo");
  await b.getByRole("link", { name: "Requests" }).click();
  const awaiting = b.getByRole("table", { name: "Awaiting response" });
  const fromA = awaiting.getByRole("row").filter({ hasText: "Hospital A" });
  await expect(fromA).toHaveCount(1);
  await expect(fromA).toContainText("Surgical Kit A");
  await expect(fromA).toContainText("850 kit");
  await confirm(b, "Decline", fromA);
  await expect(b.getByText("Request declined.")).toBeVisible();
  await expect(fromA).toHaveCount(0);
  const answered = b.getByRole("table", { name: "Answered and closed" });
  await expect(answered.getByRole("row").filter({ hasText: "Hospital A" }).first()).toContainText(
    "Declined",
  );

  // Step 4: A's page follows live: run #2 without B; C, D and E still fail; BUY from Y.
  await expect(a.getByText("Latest match run #2")).toBeVisible({ timeout: 30_000 });
  await expect(a.getByText(/^A source declined · /)).toBeVisible();
  await expect(a.getByTestId("plan")).toHaveText("Planned: Buy — 850 kit from Supplier Y");
  await expect(toB).toContainText("Declined");
  await expect(toB.getByTestId("decline-reason")).toHaveText("No reason was entered.");
  await expect(eligible).not.toContainText("Hospital B");
  await expect(reasonsOf(a, "Hospital C")).toHaveText(["Only 100 transferable; 850 needed"]);
  await expect(reasonsOf(a, "Hospital D")).toHaveText(["Expires in 12 days; 30 required"]);
  await expect(reasonsOf(a, "Hospital E")).toHaveText(["Not authorized to supply this product"]);

  // Step 5: A's approver sees Y recommended, X the alternative, and approves the purchase.
  const approver = await signIn(browser, "hospital", "approver@hospital-a.demo");
  await approver.goto(`${APP.hospital}/shortages/${shortageId}`);
  const panel = approver.getByTestId("decision-panel");
  await expect(panel.getByTestId("recommendation-type")).toHaveText("Buy");
  await expect(panel.getByRole("table", { name: "Recommended sources" })).toContainText(
    "Supplier Y",
  );
  await expect(panel.getByRole("table", { name: "Recommended sources" })).toContainText("₹28.00");
  await expect(panel).toContainText("Supplier X");
  await expect(panel).toContainText("₹14.00");
  const approved = approver.waitForResponse(
    (r) => r.url().endsWith("/approve") && r.request().method() === "POST",
  );
  await confirm(approver, "Approve purchase", panel);
  const { purchase_order_id: poId } = (await (await approved).json()) as {
    purchase_order_id: string;
  };
  await expect(approver.getByTestId("approved-message")).toHaveText(
    "The order has gone to the supplier.",
  );

  // Step 6: Supplier Y acknowledges and dispatches (supplier-web) ...
  const y = await signIn(browser, "supplier", "supplier.desk@supplier-y.demo");
  await y.getByRole("link", { name: "Purchase orders" }).click();
  const order = y.getByTestId(`order-${poId}`);
  await expect(order).toContainText("Surgical Kit A");
  await expect(order).toContainText("Hospital A");
  await order.getByRole("link", { name: "Details" }).click();
  await expect(y.getByText("Order for Hospital A")).toBeVisible();
  await confirm(y, "Acknowledge");
  await expect(y.getByText("Order acknowledged.")).toBeVisible();
  const dispatched = y.waitForResponse(
    (r) => r.url().endsWith(`/purchase-orders/${poId}/dispatch`) && r.ok(),
  );
  await confirm(y, "Mark dispatched");
  const { shipment_id: shipmentId } = (await (await dispatched).json()) as {
    shipment_id: string;
  };
  await expect(y.getByText("Order dispatched. The shipment is created.")).toBeVisible();

  // ... SwiftMed's dispatcher assigns Ravi; he picks up, travels and delivers (delivery-web).
  const dispatcher = await signIn(browser, "delivery", "dispatcher@swiftmed.demo");
  await expect(dispatcher.getByTestId(`shipment-${shipmentId}`)).toContainText("Surgical Kit A");
  await assign(dispatcher, shipmentId, "Ravi (+91 90000 00001)", "KA-01-SM-0001");
  const driver = await signIn(browser, "delivery", "driver@swiftmed.demo");
  await expect(driver).toHaveURL(`${APP.delivery}/driver`);
  await drive(driver, shipmentId);

  // Step 7: A's receiver records 790 received and accepted of the 850 expected.
  const receiver = await signIn(browser, "hospital", "receiver@hospital-a.demo");
  await receiver.getByRole("link", { name: "Deliveries" }).click();
  const row = receiver.getByTestId(`shipment-${shipmentId}`);
  await expect(row).toContainText("Delivered");
  await row.getByRole("link", { name: "Receive" }).click();
  const receipt = await recordReceipt(receiver, shipmentId, {
    expected: "850 kit",
    received: 790,
    accepted: 790,
    rejected: 0,
  });
  expect(receipt.reconciliation.outcome).toBe("PARTIAL");
  const outcome = receiver.getByTestId("receipt-outcome");
  await expect(outcome.getByTestId("outcome")).toHaveText(
    "Reconciled: the shortage is partially resolved. This delivery was 60 kit short of the 850 kit expected.",
  );
  await expect(outcome.getByTestId("residual")).toContainText("A residual shortage was opened");

  // The approver's page follows live: PARTIALLY_RESOLVED. The residual of 60 matches by itself.
  await expect(approver.getByText("Partially resolved").first()).toBeVisible({ timeout: 20_000 });
  const residualId = receipt.reconciliation.residual_shortage_id!;
  await approver.goto(`${APP.hospital}/shortages/${residualId}`);
  await expect(approver.getByTestId("shortfall")).toHaveText("60 kit");
  await expect(approver.getByRole("link", { name: "An earlier shortage" })).toHaveAttribute(
    "href",
    `/shortages/${shortageId}`,
  );
  await expect(approver.getByText("Latest match run #1")).toBeVisible();
  // Hospital C's 100 transferable now covers the 60.
  await expect(approver.getByTestId("plan")).toHaveText(
    "Planned: Transfer — 60 kit from Hospital C",
  );
  // B declined the parent, so the residual inherits its exclusion (business-rules §9).
  await expect(approver.getByRole("table", { name: "Eligible sources" })).not.toContainText(
    "Hospital B",
  );

  // Step 8: the audit trail shows every step; B's decline is SYSTEM, "No reason was entered."
  await approver.goto(`${APP.hospital}/shortages/${shortageId}`);
  await approver.getByRole("tab", { name: "Audit trail" }).click();
  const trail = approver.getByRole("list", { name: "Audit trail" });
  const actions = trail.getByTestId("audit-action");
  for (const step of [
    "Shortage created (Open)",
    "Match run created",
    "Source request created (Requested)",
    "Source request: Requested → Declined",
    "Recommendation created (Pending)",
    "Recommendation: Pending → Approved",
    "Purchase order created (Sent)",
    "Purchase order: Sent → Acknowledged",
    "Purchase order: Acknowledged → Dispatched",
    "Shipment created (Created)",
    "Shipment: Created → Assigned",
    "Shipment: Assigned → Picked up",
    "Shipment: Picked up → In transit",
    "Shipment: In transit → Delivered",
    "Receipt recorded",
    "Shortage: In fulfillment → Received",
    "Reconciliation completed",
    "Shortage: Received → Partially resolved",
  ])
    await expect(actions.filter({ hasText: step }).first()).toBeVisible();
  const decline = trail
    .getByTestId("audit-row")
    .filter({ hasText: "Source request: Requested → Declined" });
  await expect(decline).toHaveCount(1);
  await expect(decline.getByTestId("audit-actor")).toContainText("Hospital B");
  await expect(decline.getByTestId("audit-actor")).toContainText("Other organization");
  await expect(decline).toHaveAttribute("data-reason-source", "SYSTEM");
  await expect(decline.getByTestId("recorded-reason")).toHaveText("SystemNo reason was entered.");
});
