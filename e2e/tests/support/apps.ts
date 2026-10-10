// Shared steps for the demo scenario specs (S20): one browser context per person, each signed
// in through its app's own sign-in page, as in docs/demo-runbook.md. `make e2e` starts its hub
// with E2E_LOGIN_RATE_LIMIT logins per minute (the default 5 would refuse the sixth person).
import { type Browser, expect, type Locator, type Page } from "@playwright/test";

export const PASSWORD = "demo1234"; // app/seed.py: $SEED_PASSWORD or this
export const APP = {
  hospital: "http://localhost:5173",
  supplier: "http://localhost:5174",
  delivery: "http://localhost:5175",
} as const;
export type App = keyof typeof APP;

/** A new browser context (one person) signed in to `app` as `email`; map tiles are not
 *  fetched. */
export async function signIn(browser: Browser, app: App, email: string): Promise<Page> {
  const context = await browser.newContext({ timezoneId: "Asia/Kolkata" });
  await context.route(/tile\.openstreetmap\.org/, (route) => route.abort());
  const page = await context.newPage();
  await page.goto(APP[app]);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
  return page;
}

/** Clicks `button` (inside `scope`), then the confirm button of the dialog it opens (by default
 *  the same name). */
export async function confirm(
  page: Page,
  button: string,
  scope: Page | Locator = page,
  confirmLabel = button,
) {
  await scope.getByRole("button", { name: button }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: confirmLabel }).click();
  await expect(dialog).toHaveCount(0);
}

/** The value a `datetime-local` input takes for now + `hours`, in the page's time zone. */
export function inHours(page: Page, hours: number): Promise<string> {
  return page.evaluate((h) => {
    const d = new Date(Date.now() + h * 3_600_000);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); // datetime-local is local time
    return d.toISOString().slice(0, 16);
  }, hours);
}

export type NewShortage = {
  product: string; // the Product option's label, e.g. "Surgical Kit A (SURG-KIT-A)"
  required: number;
  usable: number;
  hours: number; // required by now + hours
  priority: "CRITICAL" | "ROUTINE";
  minShelfLifeDays?: number;
};

/** Reports a shortage through the "New shortage" dialog and returns its id. Leaves the page on
 *  the shortage's detail screen. `shortfall` is the hub's figure the dialog must show. */
export async function reportShortage(
  page: Page,
  s: NewShortage,
  shortfall: string,
): Promise<string> {
  await page.getByRole("link", { name: "Shortages" }).click();
  await page.getByRole("button", { name: "New shortage" }).click();
  const form = page.getByRole("dialog");
  await form.getByLabel("Product").selectOption({ label: s.product });
  await form.getByLabel("Quantity required").fill(String(s.required));
  await form.getByLabel("Usable stock on hand").fill(String(s.usable));
  await form.getByLabel("Required by").fill(await inHours(page, s.hours));
  await form.getByLabel("Priority").selectOption(s.priority);
  if (s.minShelfLifeDays !== undefined)
    await form.getByLabel("Minimum shelf life (days)").fill(String(s.minShelfLifeDays));
  const created = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/shortages" && r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "Report shortage" }).click();
  const { id } = (await (await created).json()) as { id: string };
  await expect(form.getByTestId("shortfall")).toHaveText(shortfall);
  await form.getByRole("link", { name: "View shortage" }).click();
  await expect(page).toHaveURL(`${APP.hospital}/shortages/${id}`);
  return id;
}

/** An ISO date (YYYY-MM-DD) `days` from today, for the receipt's expiry date. */
export const isoDateIn = (days: number) =>
  new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

/** Records a receipt on the receive screen; returns the hub's receipt. */
export async function recordReceipt(
  page: Page,
  shipmentId: string,
  r: {
    expected: string;
    received: number;
    accepted: number;
    rejected: number;
    note?: string;
  },
) {
  const form = page.getByRole("form", { name: "Receipt" });
  await expect(form.getByTestId("expected")).toHaveText(r.expected);
  await form.getByLabel("Received", { exact: true }).fill(String(r.received));
  await form.getByLabel("Accepted", { exact: true }).fill(String(r.accepted));
  await form.getByLabel("Rejected", { exact: true }).fill(String(r.rejected));
  await form.getByLabel("Condition").selectOption("GOOD");
  await form.getByLabel("Expiry date of accepted stock").fill(isoDateIn(365));
  if (r.note !== undefined) await form.getByLabel(/^Inspection note/).fill(r.note);
  const recorded = page.waitForResponse(
    (res) => res.url().endsWith(`/shipments/${shipmentId}/receipt`) && res.status() === 201,
  );
  await form.getByRole("button", { name: "Record receipt" }).click();
  return (await (await recorded).json()) as {
    reconciliation: { outcome: string; residual_shortage_id: string | null };
  };
}

/** The driver's job card for `shipmentId`: Picked up, In transit, Delivered (or a part). */
export async function drive(
  driver: Page,
  shipmentId: string,
  steps: readonly (readonly [string, string])[] = [
    ["Picked up", "Pickup recorded."],
    ["In transit", "Marked in transit."],
    ["Delivered", "Delivery recorded."],
  ],
) {
  const job = driver.getByTestId(`job-${shipmentId}`);
  for (const [step, done] of steps) {
    await expect(job.getByRole("button")).toHaveText([step]);
    await confirm(driver, step, job);
    await expect(driver.getByText(done).first()).toBeVisible();
  }
}

/** The dispatcher assigns `driver` and `vehicle` (option labels) from the dispatch board. */
export async function assign(
  dispatcher: Page,
  shipmentId: string,
  driver: string,
  vehicle: string,
) {
  await dispatcher.goto(APP.delivery);
  const card = dispatcher.getByTestId(`shipment-${shipmentId}`);
  await card.getByRole("button", { name: "Assign" }).click();
  const dialog = dispatcher.getByRole("dialog");
  await dialog.getByLabel("Driver").selectOption({ label: driver });
  await dialog.getByLabel("Vehicle").selectOption({ label: vehicle });
  await dialog.getByRole("button", { name: "Assign" }).click();
  await expect(dispatcher.getByText("Shipment assigned.", { exact: false })).toBeVisible();
  await expect(card).toHaveCount(0);
}
