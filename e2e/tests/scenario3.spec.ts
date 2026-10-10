import { expect, type Page, test } from "@playwright/test";
import { APP, assign, confirm, drive, recordReceipt, reportShortage, signIn } from "./support/apps";

// Scenario 3 (docs/specs/demo-scenarios.md): expiry surplus meets a forecast stock-out, from the
// full demo seed and its forecast run. Hospital B's IV Cannula 20G batch (1,000 on hand, safety
// 200, +55 days) is flagged with an expiry-risk excess of about 300 and B offers it to the
// network; Hospital E (120 usable, about 30 a day) sees its predicted stock-out in 4 days and
// B's surplus (`surplus.matched`, live); E reports a routine shortage for 300, matching ranks B
// first, B accepts, E approves, SwiftMed delivers and E receives; the platform's /admin page
// shows the procurement cost avoided and the units saved from expiry.
//
// Data: e2e/seed/scenarios.py, run by `make e2e` (B's earlier posts withdrawn, stock E received
// in earlier runs emptied, E's open IV Cannula 20G shortages cancelled, B and E re-forecast).
// Live updates need the hub's event publisher (`make worker`).

// Cost avoided = units accepted x the cheapest *eligible* supplier price in the match run that
// chose B (api-and-events.md, Network metrics). For IV Cannula 20G only Supplier Y passes the
// gates in that run (Z at ₹7.76 and X at ₹8.00 do not): 110% of ₹8.00 (app/seed.py, BASE_PRICE
// and SUPPLIER_TERMS).
const CHEAPEST_ELIGIBLE_IV_PRICE_PAISE = 880;
const SHORTAGE_QTY = 300;

const rupees = (text: string) => Math.round(Number(text.replace(/[₹,]/g, "")) * 100);

/** The /admin figures this test checks: paise avoided and units saved. */
async function metrics(admin: Page) {
  const value = (id: string) =>
    admin.getByTestId(`metric-${id}`).getByTestId("value").textContent();
  return {
    costPaise: rupees((await value("cost")) ?? ""),
    unitsSaved: Number(((await value("expiry")) ?? "").replace(/,/g, "")),
  };
}

test("Scenario 3: B's expiry surplus meets E's forecast stock-out; /admin shows the savings", async ({
  browser,
}, testInfo) => {
  test.setTimeout(240_000);

  // Hospital E (store manager) first: the stock-out is predicted, nothing is offered yet.
  const e = await signIn(browser, "hospital", "store.manager@hospital-e.demo");
  const stockouts = e.getByRole("list", { name: "Predicted stock-outs" });
  const ivStockout = stockouts.getByRole("listitem").filter({ hasText: "IV Cannula 20G" });
  await expect(ivStockout.getByTestId("stockout")).toHaveText(/\(in 4 days\)$/);
  await expect(e.getByText("No surplus offered to you")).toBeVisible();

  // Step 1: the forecast flags B's batch; B's dashboard suggests offering the excess.
  const b = await signIn(browser, "hospital", "store.manager@hospital-b.demo");
  await expect(b.getByTestId("expiry-risk-count")).toHaveText("1");
  const suggestion = b
    .getByRole("list", { name: "Expiry-risk suggestions" })
    .getByRole("listitem")
    .filter({ hasText: "IV Cannula 20G" });
  await expect(suggestion).toContainText(/Offer \d+ each to the network: IV Cannula 20G/);
  await expect(suggestion.getByTestId("synthetic")).toHaveText("Synthetic history");
  const excess = Number(
    /Offer (\d+) each/.exec((await suggestion.textContent()) ?? "")?.[1] ?? "NaN",
  );
  // demo-scenarios.md: "about 500" used before expiry, so an excess of about 300 (the hub's own
  // Scenario 3 test allows 300 ± 5; the seeded history gives 296-298 by weekday).
  expect(Math.abs(excess - 300)).toBeLessThanOrEqual(5);
  testInfo.annotations.push({ type: "expiry-risk excess", description: String(excess) });

  await b.getByRole("link", { name: "Forecasts" }).click();
  const risks = b.getByRole("table", { name: "Expiry-risk batches" });
  const riskRow = risks.getByRole("row").filter({ hasText: "IV Cannula 20G" });
  await expect(riskRow).toContainText("SEED-1");
  await expect(riskRow.getByTestId("excess")).toHaveText(`${excess} each`);
  await riskRow.getByRole("button", { name: "Offer to network" }).click();
  const offer = b.getByRole("dialog");
  await expect(offer).toContainText(`Offer ${excess} each to the network?`);
  await offer.getByRole("button", { name: "Offer to network" }).click();
  await expect(b.getByText("Offered to the network.")).toBeVisible();
  const posts = b.getByRole("table", { name: "Your surplus posts" });
  // Posts from earlier runs show as Withdrawn (e2e/seed/scenarios.py); this one is live.
  await expect(
    posts.getByRole("row").filter({ hasText: "IV Cannula 20G" }).filter({ hasText: "Matched" }),
  ).toHaveCount(1);

  // Step 2: E's dashboard shows B's surplus as soon as the hub matches it (surplus.matched).
  const offered = e
    .getByRole("list", { name: "Surplus offered to you" })
    .getByRole("listitem")
    .filter({ hasText: "Hospital B" });
  await expect(offered).toContainText(`Hospital B offers ${excess} each IV Cannula 20G`, {
    timeout: 20_000,
  });
  await expect(offered).toContainText("Expires in 30–59 days · Matches your predicted stock-out");

  // The platform's network metrics before the transfer.
  const admin = await signIn(browser, "hospital", "admin@care-e.demo");
  await admin.getByRole("link", { name: "Admin" }).click();
  await expect(admin.getByRole("heading", { name: "Network metrics" })).toBeVisible();
  await expect(admin.getByTestId("metric-expiry")).toBeVisible();
  const before = await metrics(admin);

  // Step 3: E reports a routine shortage for 300; matching ranks B first.
  const shortageId = await reportShortage(
    e,
    {
      product: "IV Cannula 20G (IV-CAN-20G)",
      required: SHORTAGE_QTY,
      usable: 0,
      hours: 48,
      priority: "ROUTINE",
    },
    "300 each",
  );
  await expect(e.getByTestId("plan")).toHaveText("Planned: Transfer — 300 each from Hospital B");
  const eligible = e.getByRole("table", { name: "Eligible sources" });
  await expect(eligible.getByRole("row").nth(1)).toContainText("Hospital B");

  await b.getByRole("link", { name: "Requests" }).click();
  const fromE = b
    .getByRole("table", { name: "Awaiting response" })
    .getByRole("row")
    .filter({ hasText: "Hospital E" });
  await expect(fromE).toContainText("IV Cannula 20G");
  await confirm(b, "Accept", fromE, "Accept and hold");
  await expect(b.getByText("Accepted. 300 each are on hold.")).toBeVisible();

  const approver = await signIn(browser, "hospital", "approver@hospital-e.demo");
  await approver.goto(`${APP.hospital}/shortages/${shortageId}`);
  const panel = approver.getByTestId("decision-panel");
  await expect(panel.getByTestId("recommendation-type")).toHaveText("Transfer");
  await expect(panel.getByRole("table", { name: "Recommended sources" })).toContainText(
    "Hospital B",
  );
  const approved = approver.waitForResponse(
    (r) => r.url().endsWith("/approve") && r.request().method() === "POST",
  );
  await confirm(approver, "Approve transfer", panel);
  const { shipment_ids: shipments } = (await (await approved).json()) as {
    shipment_ids: string[];
  };
  const shipmentId = shipments[0]!;
  await expect(approver.getByTestId("approved-message")).toHaveText(
    "Stock is now held at the source.",
  );

  const dispatcher = await signIn(browser, "delivery", "dispatcher@swiftmed.demo");
  await assign(dispatcher, shipmentId, "Ravi (+91 90000 00001)", "KA-01-SM-0001");
  const driver = await signIn(browser, "delivery", "driver@swiftmed.demo");
  await drive(driver, shipmentId);

  const receiver = await signIn(browser, "hospital", "receiver@hospital-e.demo");
  await receiver.getByRole("link", { name: "Deliveries" }).click();
  const row = receiver.getByTestId(`shipment-${shipmentId}`);
  await expect(row).toContainText("Delivered");
  await row.getByRole("link", { name: "Receive" }).click();
  const receipt = await recordReceipt(receiver, shipmentId, {
    expected: "300 each",
    received: SHORTAGE_QTY,
    accepted: SHORTAGE_QTY,
    rejected: 0,
  });
  expect(receipt.reconciliation.outcome).toBe("CONFIRMED");
  await expect(receiver.getByTestId("receipt-outcome").getByTestId("outcome")).toHaveText(
    "Reconciled: the shortage is resolved.",
  );

  // Step 4: /admin, refreshed: the cost avoided against the cheapest supplier price, and the
  // units saved from expiry (the smaller of the 300 held and the posted excess).
  await admin.getByRole("button", { name: "Refresh" }).click();
  await expect
    .poll(async () => (await metrics(admin)).unitsSaved)
    .toBe(before.unitsSaved + Math.min(SHORTAGE_QTY, excess));
  const after = await metrics(admin);
  expect(after.costPaise - before.costPaise).toBe(SHORTAGE_QTY * CHEAPEST_ELIGIBLE_IV_PRICE_PAISE);
  for (const id of ["time", "mix", "cost", "expiry", "cold-chain"])
    await expect(admin.getByTestId(`metric-${id}`).getByTestId("definition")).not.toBeEmpty();
  testInfo.annotations.push({
    type: "metrics",
    description: `cost avoided +${after.costPaise - before.costPaise} paise, units saved ${after.unitsSaved}`,
  });
});
