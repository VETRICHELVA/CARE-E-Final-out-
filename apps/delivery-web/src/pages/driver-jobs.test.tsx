import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Shipment, ShipmentStatus } from "../api";
import { assigned, coldShipment, detail, meAs, page, products } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { DriverJobsPage } from "./driver-jobs";

/** A fix near Hospital B. */
const FIX = { coords: { latitude: 12.9301, longitude: 77.6302, accuracy: 12 }, timestamp: 0 };

type Success = (position: typeof FIX) => void;
type Failure = (error: { code: number; message: string }) => void;

/** `navigator.geolocation` that answers each request with `answer`. */
function mockGeolocation(answer: (ok: Success, fail: Failure) => void) {
  const getCurrentPosition = vi.fn((ok: Success, fail: Failure) => answer(ok, fail));
  vi.stubGlobal("navigator", { ...navigator, geolocation: { getCurrentPosition } });
  return getCurrentPosition;
}

/** Retries `check` on real timeouts (never advancing the fake interval) until it passes. */
async function eventually(check: () => void, tries = 100) {
  for (let i = 1; ; i++) {
    try {
      check();
      return;
    } catch (e) {
      if (i >= tries) throw e;
      await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }
}

beforeEach(() => {
  // Only the ping interval is faked. Testing Library's waitFor polls with setInterval too, so
  // checks that no DOM change would wake use `eventually` below.
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const pickedUp = assigned("PICKED_UP", coldShipment, 4);
const inTransit = assigned("IN_TRANSIT", coldShipment, 5);
const delivered = assigned("DELIVERED", coldShipment, 6);
const job = assigned("ASSIGNED");

const hub = (jobs: Shipment[], extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments": (call: Call) => {
      expect(call.url.searchParams.get("assigned_to_me")).toBe("true");
      return page(jobs);
    },
    "POST /api/v1/shipments/{id}/location": (call: Call) => ({
      shipment_id: call.path.split("/")[4],
      ...JSON.parse(call.body!),
      ts: "2026-10-08T06:00:00Z",
    }),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "DRIVER") =>
  renderAs(meAs(role), <DriverJobsPage />, { path: "/driver" });

const card = (s: Shipment) => screen.findByTestId(`job-${s.id}`);
const buttonsIn = (row: HTMLElement) =>
  within(row)
    .queryAllByRole("button")
    .map((b) => b.textContent);

describe("Driver jobs", () => {
  it.each<[ShipmentStatus, string[]]>([
    ["ASSIGNED", ["Picked up"]],
    ["PICKED_UP", ["In transit"]],
    ["IN_TRANSIT", ["Delivered"]],
  ])("shows only the next step's button for a %s job", async (status, expected) => {
    const s = assigned(status);
    hub([s]);
    show();
    expect(buttonsIn(await card(s))).toEqual(expected);
  });

  it("lists delivered jobs under Completed, with no buttons", async () => {
    hub([job, delivered]);
    show();
    const done = within(await screen.findByRole("region", { name: "Completed jobs" }));
    const row = done.getByTestId(`job-${delivered.id}`);
    expect(buttonsIn(row)).toEqual([]);
    expect(within(row).getByText("Delivered")).toBeTruthy();
  });

  it("records the pickup through the hub, then refetches the jobs", async () => {
    const fake = hub([job], {
      "POST /api/v1/shipments/{id}/status": detail(assigned("PICKED_UP")),
    });
    show();
    fireEvent.click(within(await card(job)).getByRole("button", { name: "Picked up" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Record the pickup at Hospital B, Hospital B Main Store?"),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Picked up" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/shipments/${job.id}/status`);
    expect(JSON.parse(call!.body!)).toEqual({ status: "PICKED_UP" });
    await waitFor(() => expect(fake.to("GET", "/api/v1/shipments")).toHaveLength(2));
  });

  it("shows the hub's 409 when the job already moved on", async () => {
    hub([inTransit], {
      "POST /api/v1/shipments/{id}/status": hubError(
        409,
        "invalid_transition",
        "Cannot move a shipment from DELIVERED to DELIVERED.",
      ),
    });
    show();
    fireEvent.click(within(await card(inTransit)).getByRole("button", { name: "Delivered" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delivered" }));
    expect(
      await within(dialog).findByText("Cannot move a shipment from DELIVERED to DELIVERED."),
    ).toBeTruthy();
  });

  it("asks only for the caller's own jobs", async () => {
    const fake = hub([job]);
    show();
    await card(job);
    expect(fake.to("GET", "/api/v1/shipments")[0]!.url.searchParams.get("assigned_to_me")).toBe(
      "true",
    );
  });

  it("is for drivers only: a dispatcher sees no jobs and no requests are made", async () => {
    const fake = hub([job]);
    show("DISPATCHER");
    expect(await screen.findByText("For drivers")).toBeTruthy();
    expect(fake.to("GET", "/api/v1/shipments")).toHaveLength(0);
  });

  it("shows an empty state, and no sharing toggle, without jobs on the way", async () => {
    hub([delivered]);
    show();
    expect(await screen.findByText("No jobs on the way")).toBeTruthy();
    expect(screen.queryByRole("switch", { name: /Share location/ })).toBeNull();
  });

  it("shows the hub's message when the jobs fail to load", async () => {
    hub([], { "GET /api/v1/shipments": hubError(500, "internal_error", "The hub is down.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });
});

describe("Share location", () => {
  it("sends a ping for each job on the way now and every 30 s, until turned off", async () => {
    const geo = mockGeolocation((ok) => ok(FIX));
    const fake = hub([job, pickedUp, delivered]);
    show();
    const toggle = await screen.findByRole("switch", { name: /Share location/ });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(geo).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    await eventually(() =>
      expect(fake.calls.filter((c) => c.path.endsWith("/location"))).toHaveLength(2),
    );
    expect(geo).toHaveBeenCalledTimes(1);
    for (const s of [job, pickedUp]) {
      const [call] = fake.to("POST", `/api/v1/shipments/${s.id}/location`);
      expect(JSON.parse(call!.body!)).toEqual({ lat: 12.9301, lng: 77.6302 });
    }
    expect(fake.to("POST", `/api/v1/shipments/${delivered.id}/location`)).toHaveLength(0);
    expect(await screen.findByTestId("last-sent")).toBeTruthy();

    act(() => vi.advanceTimersByTime(29_999));
    expect(geo).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1));
    expect(geo).toHaveBeenCalledTimes(2);
    await eventually(() =>
      expect(fake.to("POST", `/api/v1/shipments/${job.id}/location`)).toHaveLength(2),
    );

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    act(() => vi.advanceTimersByTime(90_000));
    expect(geo).toHaveBeenCalledTimes(2);
  });

  it("shows a clear denied state and stops asking when location is blocked", async () => {
    const geo = mockGeolocation((_, fail) => fail({ code: 1, message: "User denied Geolocation" }));
    const fake = hub([job]);
    show();
    const toggle = await screen.findByRole("switch", { name: /Share location/ });
    fireEvent.click(toggle);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Location access is blocked for this site. Allow location in your browser's site settings, then turn sharing on again.",
    );
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    act(() => vi.advanceTimersByTime(60_000));
    expect(geo).toHaveBeenCalledTimes(1);
    expect(fake.calls.filter((c) => c.path.endsWith("/location"))).toHaveLength(0);
  });

  it("keeps trying when the position is briefly unavailable", async () => {
    let attempt = 0;
    const geo = mockGeolocation((ok, fail) =>
      attempt++ === 0 ? fail({ code: 3, message: "Timeout expired" }) : ok(FIX),
    );
    const fake = hub([job]);
    show();
    fireEvent.click(await screen.findByRole("switch", { name: /Share location/ }));
    expect(await screen.findByText(/Couldn't get your position/)).toBeTruthy();
    act(() => vi.advanceTimersByTime(30_000));
    expect(geo).toHaveBeenCalledTimes(2);
    await eventually(() =>
      expect(fake.to("POST", `/api/v1/shipments/${job.id}/location`)).toHaveLength(1),
    );
    await waitFor(() => expect(screen.queryByText(/Couldn't get your position/)).toBeNull());
  });

  it("shows the hub's refusal of a ping and keeps sharing", async () => {
    mockGeolocation((ok) => ok(FIX));
    hub([job], {
      "POST /api/v1/shipments/{id}/location": hubError(
        403,
        "forbidden",
        "Only the shipment's assigned driver can do this.",
      ),
    });
    show();
    const toggle = await screen.findByRole("switch", { name: /Share location/ });
    fireEvent.click(toggle);
    expect(
      await screen.findByText(
        "The hub did not take the last position: Only the shipment's assigned driver can do this.",
      ),
    ).toBeTruthy();
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("says so when the browser has no geolocation", async () => {
    vi.stubGlobal("navigator", { ...navigator, geolocation: undefined });
    hub([job]);
    show();
    fireEvent.click(await screen.findByRole("switch", { name: /Share location/ }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This browser cannot share your location.",
    );
  });
});
