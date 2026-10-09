import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// Scenario 1 step 6, supplier part (docs/specs/demo-scenarios.md), in the supplier app:
// Supplier Y acknowledges a purchase order, then marks it dispatched; only the actions the
// order's state allows are on screen at each step.
//
// Self-seeding: `e2e/seed/supplier_po.py` (run below, against the hub's DATABASE_URL) reaches a
// SENT purchase order through the hub's own services (a Nebulizer Mask BUY from Supplier Y,
// approved by Hospital A) and issues Supplier Y's desk user a session. The test then uses that
// session instead of logging in, so `make e2e` stays within the hub's 5 logins per minute.

const SUPPLIER = "http://localhost:5174";
const HUB_DIR = fileURLToPath(new URL("../../services/hub-api", import.meta.url));

type Seed = { purchase_order_id: string; access_token: string; refresh_token: string };

function seedPurchaseOrder(): Seed {
  const out = execFileSync("uv", ["run", "python", "../../e2e/seed/supplier_po.py"], {
    cwd: HUB_DIR,
    encoding: "utf8",
  });
  return JSON.parse(out.trim().split("\n").at(-1)!) as Seed;
}

test("Scenario 1 step 6: Supplier Y acknowledges and dispatches the PO", async ({ browser }) => {
  test.setTimeout(90_000);
  const seed = seedPurchaseOrder();
  const context = await browser.newContext({ timezoneId: "Asia/Kolkata" });
  // The app keeps its session in sessionStorage (packages/api-client, `useAuth`).
  await context.addInitScript(
    (tokens) => {
      if (!sessionStorage.getItem("care-e-auth"))
        sessionStorage.setItem("care-e-auth", JSON.stringify({ state: { tokens }, version: 0 }));
    },
    { access: seed.access_token, refresh: seed.refresh_token },
  );
  const page = await context.newPage();

  // The dashboard lists it among the new purchase orders.
  await page.goto(SUPPLIER);
  await expect(page.getByTestId("org-name")).toHaveText("Supplier Y");
  const newOrders = page.getByRole("region", { name: /^New purchase orders/ });
  await expect(newOrders.getByRole("link", { name: /Nebulizer Mask/ }).first()).toBeVisible();

  // SENT: Acknowledge and Reject only.
  await page.goto(`${SUPPLIER}/orders/${seed.purchase_order_id}`);
  await expect(page.getByText("Order for Hospital A")).toBeVisible();
  await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Acknowledge" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark dispatched" })).toHaveCount(0);

  await page.getByRole("button", { name: "Acknowledge" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByText("Order acknowledged.")).toBeVisible();

  // ACKNOWLEDGED: Mark dispatched and Reject.
  await expect(page.getByText("Acknowledged", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);
  await page.getByRole("button", { name: "Mark dispatched" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Mark dispatched" }).click();
  await expect(page.getByText("Order dispatched. The shipment is created.")).toBeVisible();

  // DISPATCHED: the hub created the shipment; nothing left to do.
  await expect(page.getByText("Dispatched", { exact: true })).toBeVisible();
  await expect(page.getByTestId("shipment")).toHaveText(/^Created \(/);
  await expect(page.getByTestId("no-actions")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject" })).toHaveCount(0);

  // Network demand names no hospital.
  await page.getByRole("link", { name: "Network demand" }).click();
  const demand = page.getByRole("table", { name: "Network demand" });
  await expect(demand).toContainText("Nebulizer Mask");
  await expect(demand).not.toContainText("Hospital");
});
