import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Receipt, ShipmentDetail } from "../api";
import {
  deliveredToA,
  meAs,
  NOW,
  partialReceipt,
  products,
  residual,
  RESIDUAL_ID,
  shipmentToA,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { ReceivePage } from "./receive";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const receiptPath = `/api/v1/shipments/${deliveredToA.id}/receipt`;

/** A fake hub whose shipment carries the receipt once it is recorded. */
function hub(
  shipment: ShipmentDetail = deliveredToA,
  answer: Receipt | ((call: Call) => unknown) = partialReceipt,
  extra: Record<string, unknown> = {},
) {
  let current = shipment;
  return fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments/{id}": () => current,
    "POST /api/v1/shipments/{id}/receipt": (call: Call) => {
      if (typeof answer === "function") return answer(call);
      current = { ...shipment, status: "RECONCILED", receipt: answer };
      return Response.json(answer, { status: 201 });
    },
    "GET /api/v1/shortages/{id}": residual,
    ...extra,
  });
}

const show = (role: Parameters<typeof meAs>[0] = "RECEIVER") =>
  renderAs(meAs(role), <ReceivePage />, {
    path: `/deliveries/${deliveredToA.id}/receive`,
    route: "/deliveries/:id/receive",
  });

const form = () => screen.findByRole("form", { name: "Receipt" });

/** Types the figures (blank ones are left alone) and submits. */
async function fill(values: Record<string, string>) {
  const f = await form();
  for (const [label, value] of Object.entries(values))
    fireEvent.change(within(f).getByLabelText(label), { target: { value } });
  fireEvent.click(within(f).getByRole("button", { name: "Record receipt" }));
  return f;
}

const SCENARIO_1 = {
  Received: "850",
  Accepted: "790",
  Rejected: "60",
  Condition: "DAMAGED",
  "Expiry date of accepted stock": "2027-09-30",
};

describe("Receive", () => {
  it("is only for users with receipt.record", async () => {
    const fake = hub();
    show("APPROVER");
    expect(await screen.findByText("You can't record receipts")).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
    expect(fake.to("GET", `/api/v1/shipments/${deliveredToA.id}`)).toHaveLength(0);
  });

  it("shows the expected quantity read-only, from the shipment", async () => {
    hub();
    show();
    const f = await form();
    expect(within(f).getByTestId("expected").textContent).toBe("850 kits");
    expect(within(f).queryByLabelText(/^Expected/)).toBeNull();
    expect(within(f).getByLabelText("Inspection note (optional)")).toBeTruthy();
  });

  it("refuses accepted + rejected ≠ received without calling the hub", async () => {
    const fake = hub();
    show();
    const f = await fill({ ...SCENARIO_1, Rejected: "50" });
    expect(
      await within(f).findByText("Accepted and rejected must add up to the quantity received."),
    ).toBeTruthy();
    expect(fake.to("POST", receiptPath)).toHaveLength(0);
  });

  it("refuses received > expected without calling the hub", async () => {
    const fake = hub();
    show();
    const f = await fill({ ...SCENARIO_1, Received: "900", Accepted: "840" });
    expect(
      await within(f).findByText("Received cannot be more than the 850 expected."),
    ).toBeTruthy();
    expect(fake.to("POST", receiptPath)).toHaveLength(0);
  });

  it("needs an expiry date when anything is accepted, but not when all is rejected", async () => {
    const fake = hub(deliveredToA, (call) =>
      Response.json({ ...partialReceipt, ...JSON.parse(call.body!) }, { status: 201 }),
    );
    show();
    const f = await fill({ ...SCENARIO_1, "Expiry date of accepted stock": "" });
    expect(await within(f).findByText("Enter the expiry date of the accepted stock.")).toBeTruthy();
    expect(fake.to("POST", receiptPath)).toHaveLength(0);

    await fill({ Accepted: "0", Rejected: "850" });
    await waitFor(() => expect(fake.to("POST", receiptPath)).toHaveLength(1));
    expect(JSON.parse(fake.to("POST", receiptPath)[0]!.body!)).toEqual({
      received: 850,
      accepted: 0,
      rejected: 850,
      condition: "DAMAGED",
    });
  });

  it("requires the inspection note when the hub says the shipment had an excursion", async () => {
    const fake = hub({ ...deliveredToA, inspection_note_required: true });
    show();
    const f = await form();
    const note = within(f).getByLabelText("Inspection note (required)");
    expect(note.getAttribute("aria-required")).toBe("true");
    await fill(SCENARIO_1);
    expect(
      await within(f).findByText(
        "Enter an inspection note: this shipment had a cold-chain excursion.",
      ),
    ).toBeTruthy();
    expect(fake.to("POST", receiptPath)).toHaveLength(0);

    await fill({ "Inspection note (required)": "Seals intact; probe read 6 °C on arrival." });
    await waitFor(() => expect(fake.to("POST", receiptPath)).toHaveLength(1));
    expect(JSON.parse(fake.to("POST", receiptPath)[0]!.body!).inspection_note).toBe(
      "Seals intact; probe read 6 °C on arrival.",
    );
  });

  it("shows the hub's 400 under the field it names", async () => {
    hub(deliveredToA, () =>
      Response.json(
        {
          code: "validation",
          message: "This shipment had a cold-chain excursion: enter an inspection note.",
          details: { reason: "inspection_note_required" },
        },
        { status: 400 },
      ),
    );
    show();
    const f = await fill(SCENARIO_1);
    const message = await within(f).findByText(
      "This shipment had a cold-chain excursion: enter an inspection note.",
    );
    expect(message.id).toBe("inspection_note-error");
  });

  it("shows any other hub refusal as it comes", async () => {
    hub(deliveredToA, () =>
      hubError(409, "conflict", "Batch RCV-5B000000 of this product already exists."),
    );
    show();
    const f = await fill(SCENARIO_1);
    expect(
      await within(f).findByText("Batch RCV-5B000000 of this product already exists."),
    ).toBeTruthy();
  });

  it("records 790 of 850 and shows the partial outcome with the residual's link", async () => {
    const fake = hub();
    show("ADMIN");
    await fill({
      ...SCENARIO_1,
      "Batch number (optional)": "SKA-Y-0927",
      "Reason (optional)": "60 kits had torn seals",
    });
    const outcome = await screen.findByTestId("receipt-outcome");
    expect(JSON.parse(fake.to("POST", receiptPath)[0]!.body!)).toEqual({
      received: 850,
      accepted: 790,
      rejected: 60,
      condition: "DAMAGED",
      expiry_date: "2027-09-30",
      batch_no: "SKA-Y-0927",
      reason: "60 kits had torn seals",
    });
    expect(within(outcome).getByTestId("outcome").textContent).toBe(
      "Reconciled: the shortage is partially resolved. This delivery was 60 kits short of the 850 kits expected.",
    );
    const residualLine = await within(outcome).findByText(/A residual shortage of/);
    await waitFor(() => expect(residualLine.textContent).toContain("60 kits"));
    expect(
      within(outcome).getByRole("link", { name: "View residual shortage" }).getAttribute("href"),
    ).toBe(`/shortages/${RESIDUAL_ID}`);
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("tells a receiver who cannot read shortages that a residual was opened, without a link", async () => {
    const fake = hub();
    show("RECEIVER");
    await fill(SCENARIO_1);
    const outcome = await screen.findByTestId("receipt-outcome");
    expect(within(outcome).getByTestId("residual").textContent).toContain(
      "A residual shortage was opened",
    );
    expect(within(outcome).queryByRole("link")).toBeNull();
    expect(fake.to("GET", `/api/v1/shortages/${RESIDUAL_ID}`)).toHaveLength(0);
  });

  it("shows a full receipt as resolved", async () => {
    hub(deliveredToA, {
      ...partialReceipt,
      accepted: 850,
      rejected: 0,
      condition: "GOOD",
      reconciliation: {
        ...partialReceipt.reconciliation!,
        accepted: 850,
        discrepancy: 0,
        outcome: "CONFIRMED",
        residual_shortage_id: null,
      },
    });
    show("ADMIN");
    await fill({ ...SCENARIO_1, Accepted: "850", Rejected: "0", Condition: "GOOD" });
    const outcome = await screen.findByTestId("receipt-outcome");
    expect(within(outcome).getByTestId("outcome").textContent).toBe(
      "Reconciled: the shortage is resolved.",
    );
    expect(within(outcome).queryByTestId("residual")).toBeNull();
  });

  it("says when the shortage waits for its other shipments", async () => {
    hub(deliveredToA, { ...partialReceipt, reconciliation: null });
    show();
    await fill(SCENARIO_1);
    expect((await screen.findByTestId("outcome")).textContent).toMatch(/^Not reconciled yet/);
  });

  it("shows the recorded receipt instead of the form once there is one", async () => {
    hub({ ...deliveredToA, status: "RECONCILED", receipt: partialReceipt });
    show();
    const outcome = await screen.findByTestId("receipt-outcome");
    expect(within(outcome).getByText("790 kits")).toBeTruthy();
    expect(within(outcome).getByText("Damaged")).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("offers no form before the shipment is delivered", async () => {
    hub(shipmentToA);
    show();
    expect(await screen.findByText("Not delivered yet")).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("offers no form for a shipment to another organization", async () => {
    hub({ ...deliveredToA, to_org_id: "0b000000-0000-4000-8000-0000000000d0" });
    show();
    expect(await screen.findByText("This shipment is not coming to your hospital")).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
  });
});
