import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { type Browser, expect, type Page, test } from "@playwright/test";

// S12 acceptance: Scenario 1 from purchase approval to the residual shortage (steps 4-7,
// docs/specs/demo-scenarios.md), across the three apps. Hospital A's approver approves the
// BUY from Supplier Y (hospital-web); Supplier Y acknowledges and dispatches (supplier-web);
// SwiftMed's dispatcher assigns Priya, who picks up and delivers (delivery-web); Hospital A's
// receiver accepts 790 of the 850 (hospital-web), so the shortage is PARTIALLY_RESOLVED and a
// residual shortage of 60 opens and starts matching.
//
// Self-seeding: `e2e/seed/scenario1_receipt.py` (run below, against the hub's DATABASE_URL)
// reports the shortage (1,000 required, 150 usable: shortfall 850) for Sterile Drapes, which
// only Supplier Y offers, and issues each user's session, so the test does not log in (the hub
// allows 5 logins per minute). Live updates need the hub's event publisher (`make worker`).

const HOSPITAL = "http://localhost:5173";
const SUPPLIER = "http://localhost:5174";
const DELIVERY = "http://localhost:5175";
const HUB_DIR = fileURLToPath(new URL("../../services/hub-api", import.meta.url));

type Session = { access_token: string; refresh_token: string };
type Seed = {
  shortage_id: string;
  approver: Session;
  receiver: Session;
  supplier: Session;
  dispatcher: Session;
  driver: Session;
};

function seedShortage(): Seed {
  const out = execFileSync("uv", ["run", "python", "../../e2e/seed/scenario1_receipt.py"], {
    cwd: HUB_DIR,
    encoding: "utf8",
  });
  return JSON.parse(out.trim().split("\n").at(-1)!) as Seed;
}

/** A browser context signed in with `session`; map tiles are not fetched. */
async function signedIn(browser: Browser, session: Session): Promise<Page> {
  const context = await browser.newContext({ timezoneId: "Asia/Kolkata" });
  // The apps keep their session in sessionStorage (packages/api-client, `useAuth`).
  await context.addInitScript(
    (tokens) => {
      if (!sessionStorage.getItem("care-e-auth"))
        sessionStorage.setItem("care-e-auth", JSON.stringify({ state: { tokens }, version: 0 }));
    },
    { access: session.access_token, refresh: session.refresh_token },
  );
  await context.route(/tile\.openstreetmap\.org/, (route) => route.abort());
  return context.newPage();
}

/** Clicks `button`, then the same-named confirm button in its dialog. */
async function confirm(page: Page, button: string, scope = page.locator("body")) {
  await scope.getByRole("button", { name: button }).click();
  await page.getByRole("dialog").getByRole("button", { name: button }).click();
}

test("Scenario 1: BUY approved, dispatched, delivered; 790 of 850 accepted → residual of 60", async ({
  browser,
}) => {
  test.setTimeout(180_000);
  const seed = seedShortage();

  // Step 4-5: the approver approves the BUY; the hub sends the PO to Supplier Y.
  const approver = await signedIn(browser, seed.approver);
  await approver.goto(`${HOSPITAL}/shortages/${seed.shortage_id}`);
  const panel = approver.getByTestId("decision-panel");
  await expect(panel.getByTestId("recommendation-type")).toHaveText("Buy");
  await expect(panel.getByRole("table", { name: "Recommended sources" })).toContainText(
    "Supplier Y",
  );
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

  // Step 6: Supplier Y acknowledges and dispatches; the hub creates the shipment.
  const supplier = await signedIn(browser, seed.supplier);
  await supplier.goto(`${SUPPLIER}/orders/${poId}`);
  await expect(supplier.getByText("Order for Hospital A")).toBeVisible();
  await confirm(supplier, "Acknowledge");
  await expect(supplier.getByText("Order acknowledged.")).toBeVisible();
  const dispatched = supplier.waitForResponse(
    (r) => r.url().endsWith(`/purchase-orders/${poId}/dispatch`) && r.ok(),
  );
  await confirm(supplier, "Mark dispatched");
  const { shipment_id: shipmentId } = (await (await dispatched).json()) as {
    shipment_id: string;
  };
  await expect(supplier.getByText("Order dispatched. The shipment is created.")).toBeVisible();

  // SwiftMed's dispatcher assigns Priya; she picks up, travels and delivers.
  const dispatcher = await signedIn(browser, seed.dispatcher);
  await dispatcher.goto(DELIVERY);
  const card = dispatcher.getByTestId(`shipment-${shipmentId}`);
  await expect(card).toContainText("Sterile Drape");
  await card.getByRole("button", { name: "Assign" }).click();
  const assign = dispatcher.getByRole("dialog");
  await assign.getByLabel("Driver").selectOption({ label: "Priya (+91 90000 00002)" });
  await assign.getByLabel("Vehicle").selectOption({ label: "KA-01-SM-0001" });
  await assign.getByRole("button", { name: "Assign" }).click();
  await expect(dispatcher.getByText("Shipment assigned.", { exact: false })).toBeVisible();

  const driver = await signedIn(browser, seed.driver);
  await driver.goto(`${DELIVERY}/driver`);
  const job = driver.getByTestId(`job-${shipmentId}`);
  await expect(job).toContainText("Sterile Drape");
  for (const [step, done] of [
    ["Picked up", "Pickup recorded."],
    ["In transit", "Marked in transit."],
    ["Delivered", "Delivery recorded."],
  ] as const) {
    await confirm(driver, step, job);
    await expect(driver.getByText(done)).toBeVisible();
    await expect(driver.getByRole("dialog")).toHaveCount(0);
  }

  // Step 7: Hospital A's receiver opens the delivery and accepts 790 of the 850.
  const receiver = await signedIn(browser, seed.receiver);
  await receiver.goto(`${HOSPITAL}/deliveries`);
  const row = receiver.getByTestId(`shipment-${shipmentId}`);
  await expect(row).toContainText("Delivered");
  await row.getByRole("link", { name: "Receive" }).click();
  const form = receiver.getByRole("form", { name: "Receipt" });
  await expect(form.getByTestId("expected")).toHaveText("850 each");
  await form.getByLabel("Received", { exact: true }).fill("850");
  await form.getByLabel("Accepted", { exact: true }).fill("790");
  await form.getByLabel("Rejected", { exact: true }).fill("60");
  await form.getByLabel("Condition").selectOption("DAMAGED");
  const expiry = new Date(Date.now() + 365 * 86_400_000).toISOString().slice(0, 10);
  await form.getByLabel("Expiry date of accepted stock").fill(expiry);
  await form.getByLabel("Reason (optional)").fill("60 drapes had torn packaging");
  const recorded = receiver.waitForResponse(
    (r) => r.url().endsWith(`/shipments/${shipmentId}/receipt`) && r.status() === 201,
  );
  await form.getByRole("button", { name: "Record receipt" }).click();
  const receipt = (await (await recorded).json()) as {
    reconciliation: { outcome: string; residual_shortage_id: string };
  };
  expect(receipt.reconciliation.outcome).toBe("PARTIAL");
  const outcome = receiver.getByTestId("receipt-outcome");
  await expect(outcome.getByTestId("outcome")).toHaveText(
    "Reconciled: the shortage is partially resolved. This delivery was 60 each short of the 850 each expected.",
  );
  // A receiver cannot read shortages: told, not linked.
  await expect(outcome.getByTestId("residual")).toContainText("A residual shortage was opened");
  await receiver.goto(`${HOSPITAL}/deliveries`);
  await expect(receiver.getByTestId(`shipment-${shipmentId}`)).toContainText("Reconciled");

  // The approver's open page follows live: PARTIALLY_RESOLVED.
  await expect(approver.getByText("Partially resolved").first()).toBeVisible({ timeout: 20_000 });

  // The residual: 60 needed, linked to its parent, and matching started on its own.
  const residualId = receipt.reconciliation.residual_shortage_id;
  await approver.goto(`${HOSPITAL}/shortages/${residualId}`);
  await expect(approver.getByTestId("shortfall")).toHaveText("60 each");
  await expect(approver.getByRole("link", { name: "An earlier shortage" })).toHaveAttribute(
    "href",
    `/shortages/${seed.shortage_id}`,
  );
  await expect(approver.getByText(/^Match run #1$|^Latest match run #1$/).first()).toBeVisible();
});
