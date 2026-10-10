import { expect, type Page, test } from "@playwright/test";
import { APP, confirm, drive, recordReceipt, reportShortage, signIn } from "./support/apps";
import { startExcursion } from "./support/telemetry";

// Scenario 2 (docs/specs/demo-scenarios.md): a cold-chain transfer with an excursion, from the
// full demo seed. Hospital C reports a routine Rapid Diagnostic Kit shortage; Hospital F (500
// transferable) is recommended and accepts; C's approver approves; SwiftMed assigns the
// cold-chain van and cold box cb-01 (delivery-web); the box reads about 4 °C, then 9.1 and
// 9.4 °C: a coldchain.excursion alert in delivery-web and in Hospital C's hospital-web; readings
// recover (RECOVERED, the excursion stays on record); C's receive screen, and the hub, refuse
// the receipt until an inspection note is entered.
//
// Telemetry: the simulator through MQTT and `make ingest` when a broker is up, else the same
// readings posted to the hub's ingest endpoint (e2e/tests/support/telemetry.ts). Live updates
// and alerts need the hub's event publisher (`make worker`). Data: e2e/seed/scenarios.py, run by
// `make e2e` (C's open Rapid Diagnostic Kit shortages cancelled, cb-01 free).

const DEVICE = "cb-01";

/** The app's toasts (sonner's "Notifications" region). */
const toasts = (page: Page) => page.getByRole("region", { name: /^Notifications/ });

test("Scenario 2: cold-chain transfer F → C with an excursion; receipt needs an inspection note", async ({
  browser,
}, testInfo) => {
  test.setTimeout(300_000);

  // Step 1: Hospital C reports; the hub recommends Hospital F's 500 transferable.
  const c = await signIn(browser, "hospital", "store.manager@hospital-c.demo");
  const shortageId = await reportShortage(
    c,
    {
      product: "Rapid Diagnostic Kit (DIAG-RDK)",
      required: 200,
      usable: 0,
      hours: 48,
      priority: "ROUTINE",
      minShelfLifeDays: 60,
    },
    "200 kit",
  );
  await expect(c.getByText("Latest match run #1")).toBeVisible();
  await expect(c.getByTestId("plan")).toHaveText("Planned: Transfer — 200 kit from Hospital F");
  const eligible = c.getByRole("table", { name: "Eligible sources" });
  await expect(eligible.getByRole("row").nth(1)).toContainText("Hospital F");
  await expect(eligible.getByRole("row").nth(1)).toContainText("500 kit transferable");
  const toF = c.getByRole("table", { name: "Source requests" }).getByRole("row").filter({
    hasText: "Hospital F",
  });
  await expect(toF).toContainText("Requested");

  // Step 2: F accepts (a tentative hold), then C's approver approves the transfer.
  const f = await signIn(browser, "hospital", "store.manager@hospital-f.demo");
  await f.getByRole("link", { name: "Requests" }).click();
  const fromC = f
    .getByRole("table", { name: "Awaiting response" })
    .getByRole("row")
    .filter({ hasText: "Hospital C" });
  await expect(fromC).toContainText("Rapid Diagnostic Kit");
  await confirm(f, "Accept", fromC, "Accept and hold");
  await expect(f.getByText("Accepted. 200 kit are on hold.")).toBeVisible();

  const approver = await signIn(browser, "hospital", "approver@hospital-c.demo");
  await approver.goto(`${APP.hospital}/shortages/${shortageId}`);
  const panel = approver.getByTestId("decision-panel");
  await expect(panel.getByTestId("recommendation-type")).toHaveText("Transfer");
  await expect(panel.getByRole("table", { name: "Recommended sources" })).toContainText(
    "Hospital F",
  );
  const approved = approver.waitForResponse(
    (r) => r.url().endsWith("/approve") && r.request().method() === "POST",
  );
  await confirm(approver, "Approve transfer", panel);
  const { shipment_ids: shipments } = (await (await approved).json()) as {
    shipment_ids: string[];
  };
  expect(shipments).toHaveLength(1);
  const shipmentId = shipments[0]!;
  await expect(approver.getByTestId("approved-message")).toHaveText(
    "Stock is now held at the source.",
  );

  // Step 3: the dispatcher assigns the cold-chain van (the only kind offered) and cb-01.
  const dispatcher = await signIn(browser, "delivery", "dispatcher@swiftmed.demo");
  const card = dispatcher.getByTestId(`shipment-${shipmentId}`);
  await expect(card).toContainText("Rapid Diagnostic Kit");
  await card.getByRole("button", { name: "Assign" }).click();
  const dialog = dispatcher.getByRole("dialog");
  await expect(dialog).toContainText(
    "This shipment needs a cold-chain vehicle; only those are listed.",
  );
  await expect(dialog.getByLabel("Vehicle").locator("option")).toHaveText([
    "Choose a vehicle",
    "KA-01-SM-0002 (cold chain)",
  ]);
  await dialog.getByLabel("Driver").selectOption({ label: "Ravi (+91 90000 00001)" });
  await dialog.getByLabel("Vehicle").selectOption({ label: "KA-01-SM-0002 (cold chain)" });
  await dialog.getByRole("button", { name: "Assign" }).click();
  await expect(dispatcher.getByText("Shipment assigned.", { exact: false })).toBeVisible();
  await dispatcher.goto(`${APP.delivery}/shipments/${shipmentId}`);
  await dispatcher.getByRole("button", { name: "Attach cold box" }).click();
  const attach = dispatcher.getByRole("dialog");
  await attach.getByLabel("Cold box").selectOption({ label: DEVICE });
  await attach.getByRole("button", { name: "Attach" }).click();
  await expect(dispatcher.getByText("Cold box attached.")).toBeVisible();
  await expect(dispatcher.getByTestId("cold-box")).toContainText(DEVICE);

  const driver = await signIn(browser, "delivery", "driver@swiftmed.demo");
  await drive(driver, shipmentId, [
    ["Picked up", "Pickup recorded."],
    ["In transit", "Marked in transit."],
  ]);

  // Hospital C's receiver watches the inbound delivery.
  const receiver = await signIn(browser, "hospital", "receiver@hospital-c.demo");
  await receiver.getByRole("link", { name: "Deliveries" }).click();
  const row = receiver.getByTestId(`shipment-${shipmentId}`);
  await expect(row).toContainText("In transit");
  await expect(row).toContainText("Cold chain");

  // Step 4: telemetry near 4 °C, then 9.1 and 9.4 °C → coldchain.excursion in both apps.
  const telemetry = await startExcursion(DEVICE);
  const how =
    telemetry.mode === "mqtt"
      ? "scripts/simulate_telemetry.py --profile excursion through MQTT and iot-ingest"
      : "no MQTT broker: the excursion profile posted to POST /internal/telemetry";
  testInfo.annotations.push({ type: "telemetry", description: how });
  console.log(`Scenario 2 telemetry: ${how}`);
  try {
    // Readings reach the hub and the shipment's cold-chain panel.
    await expect(dispatcher.getByTestId("latest-reading")).toHaveText(/^[3-5]\.\d+ °C$/, {
      timeout: 30_000,
    });
    const excursionTimeout = (telemetry.warmupSeconds + 60) * 1000;
    const alert = /^Readings out of range: 9\.4 °C, above the maximum of 8(\.0)? °C\.$/;
    for (const page of [dispatcher, receiver]) {
      await expect(toasts(page).getByText(`Temperature excursion (${DEVICE})`)).toBeVisible({
        timeout: excursionTimeout,
      });
      await expect(toasts(page).getByText(alert)).toBeVisible();
    }
    // Hospital F, the sender, is not alerted in hospital-web (inbound deliveries only).
    await expect(toasts(f).getByText(`Temperature excursion (${DEVICE})`)).toHaveCount(0);
    await expect(dispatcher.getByTestId("coldchain-event-EXCURSION")).toBeVisible();
    await expect(dispatcher.getByTestId("out-of-range").first()).toBeAttached();

    // Step 5: back in range → RECOVERED; the excursion stays on record.
    for (const page of [dispatcher, receiver])
      await expect(toasts(page).getByText(`Back in range (${DEVICE})`)).toBeVisible({
        timeout: 60_000,
      });
    await expect(dispatcher.getByTestId("coldchain-event-RECOVERED")).toBeVisible();
    await expect(dispatcher.getByTestId("coldchain-event-EXCURSION")).toBeVisible();
    await expect(row.getByTestId("excursion-badge")).toHaveText("Excursion on record");

    await drive(driver, shipmentId, [["Delivered", "Delivery recorded."]]);
  } finally {
    await telemetry.stop();
    testInfo.annotations.push({ type: "readings", description: telemetry.log.join("\n") });
  }

  // Step 6: receiving needs an inspection note: the screen asks, and the hub refuses without.
  await expect(row).toContainText("Delivered");
  await row.getByRole("link", { name: "Receive" }).click();
  await expect(receiver.getByTestId("excursion-notice")).toContainText(
    "A cold-chain excursion is on record for this shipment.",
  );
  await expect(receiver.getByLabel("Inspection note (required)")).toBeVisible();
  await expect(receiver.getByTestId("coldchain-panel")).toBeVisible();
  const form = receiver.getByRole("form", { name: "Receipt" });
  await form.getByLabel("Received", { exact: true }).fill("200");
  await form.getByLabel("Accepted", { exact: true }).fill("200");
  await form.getByLabel("Rejected", { exact: true }).fill("0");
  await form.getByLabel("Condition").selectOption("GOOD");
  await form.getByRole("button", { name: "Record receipt" }).click();
  await expect(
    form.getByText(
      "Enter an inspection note: a cold-chain excursion is on record for this shipment.",
    ),
  ).toBeVisible();
  // The hub decides, not the form: the same receipt without a note is refused.
  const refused = await receiver.evaluate(async (id) => {
    const auth = JSON.parse(sessionStorage.getItem("care-e-auth")!) as {
      state: { tokens: { access: string } };
    };
    const response = await fetch(`/api/v1/shipments/${id}/receipt`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${auth.state.tokens.access}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        received: 200,
        accepted: 200,
        rejected: 0,
        condition: "GOOD",
        expiry_date: new Date(Date.now() + 365 * 86_400_000).toISOString().slice(0, 10),
      }),
    });
    return { status: response.status, body: await response.json() };
  }, shipmentId);
  expect(refused.status).toBe(400);
  expect(refused.body.details.reason).toBe("inspection_note_required");

  await receiver.reload();
  const receipt = await recordReceipt(receiver, shipmentId, {
    expected: "200 kit",
    received: 200,
    accepted: 200,
    rejected: 0,
    note: "Indicator strips normal, packaging intact; 9.4 °C for a few seconds.",
  });
  expect(receipt.reconciliation.outcome).toBe("CONFIRMED");
  await expect(receiver.getByTestId("receipt-outcome").getByTestId("outcome")).toHaveText(
    "Reconciled: the shortage is resolved.",
  );
  await expect(receiver.getByTestId("receipt-outcome")).toContainText(
    "Indicator strips normal, packaging intact",
  );

  // C's approver sees the shortage resolved, live.
  await expect(approver.getByText("Resolved", { exact: true }).first()).toBeVisible({
    timeout: 20_000,
  });
});
