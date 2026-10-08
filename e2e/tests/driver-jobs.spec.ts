import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { type Browser, expect, type Page, test } from "@playwright/test";

// S11 acceptance: the driver view works at 360 px. On a 360 × 740 phone viewport, SwiftMed's
// dispatcher assigns an unassigned shipment to Ravi, then Ravi shares his location and moves
// it through Picked up → In transit → Delivered with the big status buttons, seeing only the
// next valid step each time.
//
// Self-seeding: `e2e/seed/driver_job.py` (run below, against the hub's DATABASE_URL) reaches a
// CREATED shipment through the hub's own services (a Face Shield BUY from Supplier Y, approved,
// acknowledged and dispatched) and issues the dispatcher's and Ravi's sessions, so the test
// does not log in (the hub allows 5 logins per minute).

const DELIVERY = "http://localhost:5175";
const HUB_DIR = fileURLToPath(new URL("../../services/hub-api", import.meta.url));
const PHONE = { width: 360, height: 740 };
const FIX = { latitude: 12.86, longitude: 77.665 }; // near Supplier Y, the pickup

type Session = { access_token: string; refresh_token: string };
type Seed = { shipment_id: string; dispatcher: Session; driver: Session };

function seedShipment(): Seed {
  const out = execFileSync("uv", ["run", "python", "../../e2e/seed/driver_job.py"], {
    cwd: HUB_DIR,
    encoding: "utf8",
  });
  return JSON.parse(out.trim().split("\n").at(-1)!) as Seed;
}

/** A phone-sized context signed in with `session`; map tiles are not fetched. */
async function phone(browser: Browser, session: Session, geolocation = false): Promise<Page> {
  const context = await browser.newContext({
    viewport: PHONE,
    isMobile: true,
    hasTouch: true,
    timezoneId: "Asia/Kolkata",
    ...(geolocation ? { geolocation: FIX, permissions: ["geolocation"] } : {}),
  });
  // The app keeps its session in sessionStorage (packages/api-client, `useAuth`).
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

/** Nothing on the page is wider than the phone. */
async function expectNoSideScroll(page: Page) {
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(PHONE.width);
}

/** Taps the job's big button for `step`, confirms it, and waits for the hub's answer. */
async function step(page: Page, job: ReturnType<Page["getByTestId"]>, step: string, done: string) {
  const buttons = job.getByRole("button");
  await expect(buttons).toHaveText([step]);
  const box = await buttons.first().boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(48); // a thumb-sized target
  await buttons.first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: step }).click();
  await expect(page.getByText(done)).toBeVisible();
  await expect(dialog).toHaveCount(0);
}

test("S11: dispatcher assigns, driver moves the shipment at 360 px", async ({ browser }) => {
  test.setTimeout(120_000);
  const seed = seedShipment();

  // Dispatcher: the shipment is on the board; assign Ravi and the ordinary van.
  const desk = await phone(browser, seed.dispatcher);
  await desk.goto(DELIVERY);
  await expect(desk.getByTestId("org-name")).toHaveText("SwiftMed Logistics");
  const card = desk.getByTestId(`shipment-${seed.shipment_id}`);
  await expect(card).toContainText("Face Shield");
  await expect(card).toContainText("Supplier Y");
  await expectNoSideScroll(desk);
  await card.getByRole("button", { name: "Assign" }).click();
  const dialog = desk.getByRole("dialog");
  await dialog.getByLabel("Driver").selectOption({ label: "Ravi (+91 90000 00001)" });
  await dialog.getByLabel("Vehicle").selectOption({ label: "KA-01-SM-0001" });
  await dialog.getByRole("button", { name: "Assign" }).click();
  await expect(desk.getByText("Shipment assigned.", { exact: false })).toBeVisible();
  await expect(card).toHaveCount(0); // off the unassigned board

  // The detail shows the hub's route and ETA on the map.
  await desk.goto(`${DELIVERY}/shipments/${seed.shipment_id}`);
  await expect(desk.getByTestId("eta")).toBeVisible();
  await expect(desk.getByTestId("route")).toContainText("km");
  await expect(desk.locator(".leaflet-container")).toBeVisible();
  await expect(desk.locator("path.leaflet-interactive").first()).toBeAttached();
  await expectNoSideScroll(desk);

  // Driver: the board sends Ravi to his jobs; only "Picked up" is offered.
  const driver = await phone(browser, seed.driver, true);
  await driver.goto(DELIVERY);
  await expect(driver).toHaveURL(`${DELIVERY}/driver`);
  await expect(driver.getByRole("heading", { name: "My jobs" })).toBeVisible();
  await expect(driver.getByRole("link", { name: "Fleet" })).toHaveCount(0);
  const job = driver.getByTestId(`job-${seed.shipment_id}`);
  await expect(job).toContainText("Face Shield");
  await expectNoSideScroll(driver);

  // Share location: the phone's position goes to the hub.
  const ping = driver.waitForResponse(
    (r) => r.url().endsWith(`/shipments/${seed.shipment_id}/location`) && r.status() === 200,
  );
  const toggle = driver.getByRole("switch", { name: /Share location/ });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await ping;
  await expect(driver.getByTestId("last-sent")).toBeVisible();

  await step(driver, job, "Picked up", "Pickup recorded.");
  await step(driver, job, "In transit", "Marked in transit.");
  await step(driver, job, "Delivered", "Delivery recorded.");

  // Delivered: under Completed, with no buttons left.
  const completed = driver.getByRole("region", { name: "Completed jobs" });
  const done = completed.getByTestId(`job-${seed.shipment_id}`);
  await expect(done).toContainText("Delivered");
  await expect(done.getByRole("button")).toHaveCount(0);
  await expectNoSideScroll(driver);

  // The dispatcher's detail page shows the driver's position and the full history.
  await desk.reload();
  await expect(desk.getByTestId("live-position")).toContainText("12.86000, 77.66500");
  const history = desk.getByRole("list", { name: "Status history" });
  await expect(history.getByRole("listitem")).toHaveCount(5);
});
