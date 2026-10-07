import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { batch, facility, meAs, page, products } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { InventoryPage } from "./inventory";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/inventory/batches": page([batch]),
    "GET /api/v1/products": products,
    "GET /api/v1/orgs/{id}/facilities": page([facility]),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "STORE_MANAGER", orgType?: string) =>
  renderAs(meAs(role, orgType), <InventoryPage />, { path: "/inventory" });

describe("Inventory", () => {
  it("shows every quantity column, with the hub's transferable emphasized and read-only", async () => {
    hub();
    show();
    const row = (await screen.findByText("SKA-2026-01")).closest("tr")!;
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(
      expect.arrayContaining([
        "Product",
        "Batch",
        "On hand",
        "Reserved",
        "Allocated",
        "Safety",
        "Quarantined",
        "Held",
        "Transferable",
        "Expiry",
        "Last verified",
      ]),
    );
    const cells = within(row);
    expect(cells.getByText("Surgical Kit A")).toBeTruthy();
    expect(cells.getByText("2,500 kits")).toBeTruthy();
    expect(cells.getByText("800 kits")).toBeTruthy();
    expect(cells.getByText("200 kits")).toBeTruthy();
    expect(cells.getByText("500 kits")).toBeTruthy();
    const transferable = cells.getByTestId("transferable");
    expect(transferable.textContent).toBe("1,000 kits");
    expect(transferable.getAttribute("aria-readonly")).toBe("true");
    expect(transferable.className).toContain("font-semibold");
    expect(transferable.querySelector("input")).toBeNull();
  });

  it("shows the hub's held qty, read-only, and transferable net of it", async () => {
    hub({
      "GET /api/v1/inventory/batches": page([{ ...batch, held_qty: 850, transferable: 150 }]),
    });
    show();
    const row = (await screen.findByText("SKA-2026-01")).closest("tr")!;
    const held = within(row).getByTestId("held");
    expect(held.textContent).toBe("850 kits");
    expect(held.getAttribute("aria-readonly")).toBe("true");
    expect(held.querySelector("input")).toBeNull();
    expect(within(row).getByTestId("transferable").textContent).toBe("150 kits");
  });

  it("shows loading, then an empty state", async () => {
    hub({ "GET /api/v1/inventory/batches": page([]) });
    show();
    expect(screen.getByRole("status").textContent).toContain("Loading");
    expect(await screen.findByText("No batches yet")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    hub({ "GET /api/v1/inventory/batches": hubError(500, "internal_error", "Hub is down.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("Hub is down.");
  });

  it("offers Edit, Verify and Import only to an inventory editor in a hospital", async () => {
    hub();
    show("REQUESTER");
    await screen.findByText("SKA-2026-01");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import CSV" })).toBeNull();
    cleanup();

    hub();
    show("ADMIN", "PLATFORM"); // has the capability, but the hub takes batch writes from hospitals only
    await screen.findByText("SKA-2026-01");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    cleanup();

    hub();
    show("STORE_MANAGER");
    await screen.findByText("SKA-2026-01");
    expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verify" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import CSV" })).toBeTruthy();
  });

  it("edits a batch, sending only the changed fields and never transferable", async () => {
    const fake = hub({
      "PATCH /api/v1/inventory/batches/{id}": (c: Call) => ({
        ...batch,
        ...JSON.parse(c.body!),
        transferable: 950,
      }),
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Reserved"), { target: { value: "850" } });
    fireEvent.change(screen.getByLabelText("Unit cost (₹)"), { target: { value: "14.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("PATCH", `/api/v1/inventory/batches/${batch.id}`);
    expect(JSON.parse(call!.body!)).toEqual({ reserved: 850, unit_cost_paise: 1450 });
  });

  it("validates the edit form before calling the hub", async () => {
    const fake = hub();
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("On hand"), { target: { value: "-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Enter a whole number, 0 or more.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("On hand"), { target: { value: "2500" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Nothing has changed.")).toBeTruthy();
    expect(fake.calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  it("shows the hub's message when an edit is refused", async () => {
    hub({
      "PATCH /api/v1/inventory/batches/{id}": hubError(
        409,
        "conflict",
        "A batch with this number already exists here.",
      ),
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Batch number"), { target: { value: "DUP" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A batch with this number already exists here.")).toBeTruthy();
  });

  it("records a count with the method the user chose", async () => {
    const fake = hub({ "POST /api/v1/inventory/batches/{id}/verify": batch });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    fireEvent.click(screen.getByRole("button", { name: "Record count" }));
    expect(await screen.findByText("Choose how you counted.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Count method"), { target: { value: "SCAN" } });
    fireEvent.change(screen.getByLabelText("Counted quantity"), { target: { value: "2480" } });
    fireEvent.click(screen.getByRole("button", { name: "Record count" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/inventory/batches/${batch.id}/verify`);
    expect(JSON.parse(call!.body!)).toEqual({ method: "SCAN", counted_qty: 2480 });
  });

  it("imports a CSV as raw text/csv and reports each rejected row", async () => {
    const csv =
      "product_code,batch_no,on_hand,expiry_date,unit_cost_paise\nSURG-KIT-A,B1,10,2027-01-01,100\n";
    const fake = hub({
      "POST /api/v1/inventory/batches/import": {
        inserted: 1,
        errors: [
          { line: 3, message: "Unknown product code 'NOPE'." },
          { line: 4, message: "on_hand must be a whole number, 0 or more." },
        ],
      },
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Import CSV" }));
    const input = await screen.findByLabelText("CSV file");
    fireEvent.change(input, {
      target: { files: [new File([csv], "stock.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    expect((await screen.findByRole("status")).textContent).toBe("Imported 1 batch.");
    const report = screen.getByRole("table", { name: "Rows not imported" });
    expect(within(report).getByText("Unknown product code 'NOPE'.")).toBeTruthy();
    expect(within(report).getByText("on_hand must be a whole number, 0 or more.")).toBeTruthy();

    const [call] = fake.to("POST", "/api/v1/inventory/batches/import");
    expect(call!.url.searchParams.get("facility_id")).toBe(facility.id);
    expect(call!.headers.get("content-type")).toBe("text/csv");
    expect(call!.body).toBe(csv);
  });

  it("asks for a file before importing", async () => {
    const fake = hub();
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Import CSV" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    expect(await screen.findByText("Choose a CSV file.")).toBeTruthy();
    expect(fake.to("POST", "/api/v1/inventory/batches/import")).toHaveLength(0);
  });
});
