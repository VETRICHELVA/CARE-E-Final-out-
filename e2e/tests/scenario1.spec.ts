import { type Browser, expect, type Page, test } from "@playwright/test";

// Scenario 1, steps 1-3 (docs/specs/demo-scenarios.md), in the hospital app with two browser
// contexts: Hospital A reports a shortage and sees B eligible and C, D, E rejected with the
// hub's reasons; Hospital B declines in its own context; A's detail page shows the re-run.
//
// The dev seed has no batches, offers or authorizations yet, so `make e2e` first runs
// `e2e/seed/scenario1.py`, which adds Scenario 1's and cancels Hospital A's open Surgical Kit A
// shortages from earlier runs. Running Playwright directly? Run that script first:
//   cd services/hub-api && uv run python ../../e2e/seed/scenario1.py

const PASSWORD = "demo1234";
const HOSPITAL = "http://localhost:5173";

/** One login per context: the hub allows 5 logins per minute per IP. */
async function signIn(browser: Browser, email: string): Promise<Page> {
  const context = await browser.newContext({ timezoneId: "Asia/Kolkata" });
  const page = await context.newPage();
  await page.goto(HOSPITAL);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
  return page;
}

/** The rejected candidate's failed-gate reasons, as the page shows them. */
const reasonsOf = (page: Page, source: string) =>
  page
    .getByRole("list", { name: "Rejected sources" })
    .locator(":scope > li")
    .filter({ hasText: source })
    .getByTestId("reason");

test("Scenario 1: A reports, B declines, A's match re-runs without B", async ({ browser }) => {
  test.setTimeout(90_000);

  // Step 1: Hospital A reports the shortage; the hub computes the shortfall.
  const a = await signIn(browser, "store.manager@hospital-a.demo");
  await a.getByRole("link", { name: "Shortages" }).click();
  await a.getByRole("button", { name: "New shortage" }).click();
  const form = a.getByRole("dialog");
  await form.getByLabel("Product").selectOption({ label: "Surgical Kit A (SURG-KIT-A)" });
  await form.getByLabel("Quantity required").fill("1000");
  await form.getByLabel("Usable stock on hand").fill("150");
  const in72h = await a.evaluate(() => {
    const d = new Date(Date.now() + 72 * 3_600_000);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); // datetime-local is local time
    return d.toISOString().slice(0, 16);
  });
  await form.getByLabel("Required by").fill(in72h);
  await form.getByLabel("Priority").selectOption("CRITICAL");
  await form.getByLabel("Minimum shelf life (days)").fill("30");
  await form.getByRole("button", { name: "Report shortage" }).click();
  await expect(form.getByTestId("shortfall")).toHaveText("850 kit");
  await form.getByRole("link", { name: "View shortage" }).click();

  // Step 2: B is eligible and asked; C, D and E are rejected with the hub's reasons.
  await expect(a.getByText("Latest match run #1")).toBeVisible();
  await expect(a.getByTestId("plan")).toHaveText("Planned: Transfer — 850 kit from Hospital B");
  const eligible = a.getByRole("table", { name: "Eligible sources" });
  await expect(eligible.getByRole("row").nth(1)).toContainText("Hospital B");
  await expect(eligible).toContainText("1,000 kit transferable");
  await expect(reasonsOf(a, "Hospital C")).toHaveText(["Only 100 transferable; 850 needed"]);
  // 12 days, or 11 once the estimated arrival falls on the next UTC day (S05 → S20 follow-up).
  await expect(reasonsOf(a, "Hospital D")).toHaveText([/^Expires in 1[12] days; 30 required$/]);
  await expect(reasonsOf(a, "Hospital E")).toHaveText(["Not authorized to supply this product"]);
  const requests = a.getByRole("table", { name: "Source requests" });
  const toB = requests.getByRole("row").filter({ hasText: "Hospital B" });
  await expect(toB).toContainText("Requested");
  await expect(toB.getByTestId("countdown")).toHaveText(/^1[45]:\d\d left$/);
  await expect(a.getByRole("button", { name: "Re-run match" })).toHaveCount(0);

  // Step 3: Hospital B declines, without a reason, in a second browser context.
  const b = await signIn(browser, "store.manager@hospital-b.demo");
  await b.getByRole("link", { name: "Requests" }).click();
  const awaiting = b.getByRole("table", { name: "Awaiting response" });
  const fromA = awaiting.getByRole("row").filter({ hasText: "Hospital A" });
  await expect(fromA).toHaveCount(1);
  await expect(fromA).toContainText("Surgical Kit A");
  await expect(fromA).toContainText("850 kit");
  await expect(fromA.getByTestId("countdown")).toHaveText(/ left$/);
  await fromA.getByRole("button", { name: "Decline" }).click();
  await b.getByRole("dialog").getByRole("button", { name: "Decline" }).click();
  await expect(b.getByText("Request declined.")).toBeVisible();
  await expect(b.getByText("No requests awaiting your response")).toBeVisible();
  const answered = b.getByRole("table", { name: "Answered and closed" });
  await expect(answered.getByRole("row").filter({ hasText: "Hospital A" }).first()).toContainText(
    "Declined",
  );

  // A's detail page picks up the re-run (polling until S07's live updates) without B.
  await expect(a.getByText("Latest match run #2")).toBeVisible({ timeout: 30_000 });
  await expect(a.getByText(/^A source declined · /)).toBeVisible();
  // Step 4 starts here: with B left out, the plan is to buy from Supplier Y (earliest ETA).
  await expect(a.getByTestId("plan")).toHaveText("Planned: Buy — 850 kit from Supplier Y");
  await expect(toB).toContainText("Declined");
  await expect(toB.getByTestId("decline-reason")).toHaveText("No reason was entered.");
  await expect(eligible).not.toContainText("Hospital B");
  await expect(reasonsOf(a, "Hospital C")).toHaveText(["Only 100 transferable; 850 needed"]);
  await expect(reasonsOf(a, "Hospital E")).toHaveText(["Not authorized to supply this product"]);
});
