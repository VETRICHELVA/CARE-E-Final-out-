// Chat ordering (S17): the AI service only drafts; the user's own click sends POST /shortages.
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Product } from "../api";
import { facility, kitA, meAs, page, shortage } from "../test/fixtures";
import { type Call, fakeHub, renderAs } from "../test/hub";
import { CopilotPanel } from "./copilot-panel";
import type { ChatDraftReply } from "./copilot-api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const rdk: Product = {
  ...kitA,
  id: "9a000000-0000-4000-8000-000000000002",
  code: "DIAG-RDK",
  name: "Rapid Diagnostic Kit",
  requires_cold_chain: true,
  default_min_shelf_life_days: 60,
};

const CLEAR: ChatDraftReply = {
  draft: {
    product_id: kitA.id,
    product_code: "SURG-KIT-A",
    product_name: "Surgical Kit A",
    unit: "kit",
    qty_required: 850,
    qty_local_usable: 0,
    required_by: "2026-10-09T23:59:00+05:30",
    required_by_display: "Friday 9 October 2026, 23:59 (Asia/Kolkata)",
    required_by_text: "by fri",
    priority: "ROUTINE",
    min_shelf_life_days: 30,
    notes: "for ICU",
  },
  missing_fields: ["priority", "min_shelf_life_days", "qty_local_usable"],
  product_candidates: [],
  question: null,
  assumptions: ["No time of day was given, so the end of that day (23:59) is used."],
  tool_trace: [
    {
      tool: "search_products",
      input: { q: "SK-A" },
      ok: true,
      label: 'product search "SK-A"',
      result: {},
      from_context: false,
    },
  ],
};

const AMBIGUOUS: ChatDraftReply = {
  ...CLEAR,
  draft: {
    ...CLEAR.draft!,
    product_id: null,
    product_code: null,
    product_name: null,
    unit: null,
    qty_required: 100,
    min_shelf_life_days: null,
    notes: null,
  },
  missing_fields: ["product_id", "priority", "min_shelf_life_days", "qty_local_usable"],
  product_candidates: [
    {
      product_id: rdk.id,
      code: "DIAG-RDK",
      name: "Rapid Diagnostic Kit",
      unit: "kit",
      default_min_shelf_life_days: 60,
      score: 0.667,
    },
    {
      product_id: kitA.id,
      code: "SURG-KIT-A",
      name: "Surgical Kit A",
      unit: "kit",
      default_min_shelf_life_days: 30,
      score: 0.667,
    },
  ],
  question:
    'Which product do you mean by "kits": Rapid Diagnostic Kit (DIAG-RDK) or Surgical Kit A (SURG-KIT-A)?',
};

const created = (c: Call) =>
  Response.json(
    // The hub's own shortfall (it differs from 850 − 0 on purpose): only the hub computes it.
    { ...shortage, ...JSON.parse(c.body!), shortfall: 849 },
    { status: 201 },
  );

function hub(reply: ChatDraftReply) {
  return fakeHub({
    "GET /ai/status": { configured: true, model: "claude-opus-5-5" },
    "POST /ai/chat/draft": reply,
    "GET /api/v1/products": page([kitA, rdk]),
    "GET /api/v1/orgs/{id}/facilities": page([facility]),
    "POST /api/v1/shortages": created,
  });
}

async function order(message: string, role: Parameters<typeof meAs>[0] = "REQUESTER") {
  renderAs(meAs(role), <CopilotPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Ask copilot" }));
  fireEvent.click(await screen.findByRole("button", { name: "Order" }));
  fireEvent.change(screen.getByLabelText("Order message"), { target: { value: message } });
  fireEvent.click(screen.getByRole("button", { name: "Draft order" }));
  return screen.findByRole("form", { name: "Shortage draft" });
}

describe("Order mode", () => {
  it("is offered only to users who may create shortages", async () => {
    hub(CLEAR);
    renderAs(meAs("APPROVER"), <CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask copilot" }));
    await screen.findByLabelText("Question for the copilot");
    expect(screen.queryByRole("button", { name: "Order" })).toBeNull();
  });

  it("drafts a card and creates nothing until the user clicks", async () => {
    const fake = hub(CLEAR);
    const card = within(await order("need 850 SK-A by fri for ICU"));
    const [draftCall] = fake.to("POST", "/ai/chat/draft");
    const sent = JSON.parse(draftCall!.body!);
    expect(sent.message).toBe("need 850 SK-A by fri for ICU");
    expect(typeof sent.user_tz).toBe("string");
    expect(fake.to("POST", "/api/v1/shortages")).toHaveLength(0);

    // Every field editable and pre-filled; defaults highlighted; the date in full.
    expect((card.getByLabelText("Product") as HTMLSelectElement).value).toBe(kitA.id);
    expect((card.getByLabelText("Quantity required") as HTMLInputElement).value).toBe("850");
    expect((card.getByLabelText("Notes (optional)") as HTMLTextAreaElement).value).toBe("for ICU");
    expect(card.getByTestId("missing-priority")).toBeTruthy();
    expect(card.getByTestId("missing-qty_local_usable")).toBeTruthy();
    expect(card.queryByTestId("missing-qty_required")).toBeNull();
    const full = card.getByTestId("required-by-full").textContent!;
    expect(full).toContain("October 2026");
    expect(full).toContain('you wrote "by fri"');
    expect(card.getByTestId("assumptions").textContent).toContain("23:59");
    expect(screen.getByText('product search "SK-A"')).toBeTruthy();

    fireEvent.change(card.getByLabelText("Quantity required"), { target: { value: "900" } });
    fireEvent.click(card.getByRole("button", { name: "Create shortage" }));
    expect((await screen.findByTestId("shortfall")).textContent).toBe("849 kits");
    const [call] = fake.to("POST", "/api/v1/shortages");
    const body = JSON.parse(call!.body!);
    expect(body).toEqual({
      product_id: kitA.id,
      facility_id: facility.id,
      qty_required: 900,
      qty_local_usable: 0,
      required_by: new Date("2026-10-09T23:59:00+05:30").toISOString(),
      priority: "ROUTINE",
      min_shelf_life_days: 30,
      notes: "for ICU",
      source: "CHAT",
      status: "OPEN",
    });
    expect(body).not.toHaveProperty("shortfall");
    // Sent as the user, with the user's own hub token (never by the AI service).
    expect(call!.headers.get("Authorization")).toBe("Bearer a");
  });

  it("saves as a draft", async () => {
    const fake = hub(CLEAR);
    const card = within(await order("need 850 SK-A by fri for ICU"));
    fireEvent.click(card.getByRole("button", { name: "Save as draft" }));
    await screen.findByTestId("order-saved");
    const [call] = fake.to("POST", "/api/v1/shortages");
    expect(JSON.parse(call!.body!)).toMatchObject({ source: "CHAT", status: "DRAFT" });
    expect(screen.getByText(/Saved as a draft/)).toBeTruthy();
  });

  it("cancels without sending anything", async () => {
    const fake = hub(CLEAR);
    const card = within(await order("need 850 SK-A by fri for ICU"));
    fireEvent.click(card.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Shortage draft" })).toBeNull();
    expect(fake.to("POST", "/api/v1/shortages")).toHaveLength(0);
  });

  it("asks the user to choose when the product is ambiguous", async () => {
    const fake = hub(AMBIGUOUS);
    const card = within(await order("need 100 kits by tomorrow"));
    expect(screen.getByTestId("order-question").textContent).toContain("Which product");
    expect((card.getByLabelText("Product") as HTMLSelectElement).value).toBe("");
    expect(card.getByTestId("missing-product_id")).toBeTruthy();

    // Not chosen: the form refuses, and nothing is sent.
    fireEvent.click(card.getByRole("button", { name: "Create shortage" }));
    expect(await card.findByText("Choose a product.")).toBeTruthy();
    expect(fake.to("POST", "/api/v1/shortages")).toHaveLength(0);

    const choices = within(card.getByTestId("product-choices"));
    fireEvent.click(choices.getByRole("button", { name: "Rapid Diagnostic Kit (DIAG-RDK)" }));
    expect((card.getByLabelText("Product") as HTMLSelectElement).value).toBe(rdk.id);
    expect((card.getByLabelText("Min shelf life (days)") as HTMLInputElement).value).toBe("60");
    fireEvent.click(card.getByRole("button", { name: "Create shortage" }));
    await waitFor(() => expect(fake.to("POST", "/api/v1/shortages")).toHaveLength(1));
    const body = JSON.parse(fake.to("POST", "/api/v1/shortages")[0]!.body!);
    expect(body).toMatchObject({ product_id: rdk.id, min_shelf_life_days: 60, source: "CHAT" });
  });

  it("shows the question alone when there is nothing to draft", async () => {
    const fake = fakeHub({
      "GET /ai/status": { configured: true, model: "m" },
      "POST /ai/chat/draft": {
        ...CLEAR,
        draft: null,
        tool_trace: [],
        assumptions: [],
        question: "Your message names more than one product. Please send one per message.",
      },
    });
    renderAs(meAs("REQUESTER"), <CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask copilot" }));
    fireEvent.click(await screen.findByRole("button", { name: "Order" }));
    fireEvent.change(screen.getByLabelText("Order message"), {
      target: { value: "200 rapid kits and 500 IV cannula" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Draft order" }));
    expect((await screen.findByTestId("order-question")).textContent).toContain("one per message");
    expect(screen.queryByRole("form", { name: "Shortage draft" })).toBeNull();
    expect(fake.calls.filter((c) => c.path.startsWith("/api/v1/shortages"))).toHaveLength(0);
  });
});
