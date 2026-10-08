import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateFor } from "@care-e/api-client";
import type { Notification } from "../api";
import { escalation, meAs, NOW, page, shortage } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { NotificationBell, NotificationsPage } from "./notifications";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const NOW_ISO = new Date(NOW).toISOString();
const N1 = "a1000000-0000-4000-8000-000000000001";
const N2 = "a1000000-0000-4000-8000-000000000002";
const N3 = "a1000000-0000-4000-8000-000000000003";

/** A fake hub holding `items` (newest first); marking one read sets its `read_at`. */
function hub(items: Notification[]) {
  let current = items;
  return fakeHub({
    "GET /api/v1/notifications": (call: Call) =>
      page(
        call.url.searchParams.get("unread") === "true"
          ? current.filter((n) => n.read_at === null)
          : current,
      ),
    "POST /api/v1/notifications/{id}/read": (call: Call) => {
      const id = call.path.split("/").at(-2);
      current = current.map((n) =>
        n.id === id ? { ...n, read_at: n.read_at ?? "2026-10-07T06:05:00Z" } : n,
      );
      return current.find((n) => n.id === id);
    },
  });
}

const show = (role: Parameters<typeof meAs>[0] = "APPROVER") =>
  renderAs(
    meAs(role),
    <>
      <NotificationBell />
      <NotificationsPage />
    </>,
    { path: "/notifications" },
  );

const bell = () => screen.getByRole("link", { name: /^Notifications/ });

describe("Notifications", () => {
  it("shows the unread count on the bell", async () => {
    const fake = hub([escalation(N1), escalation(N2), escalation(N3, { read_at: NOW_ISO })]);
    show();
    expect((await screen.findByTestId("unread-count")).textContent).toBe("2");
    expect(bell().getAttribute("aria-label")).toBe("Notifications, 2 unread");
    expect(bell().getAttribute("href")).toBe("/notifications");
    const unreadCalls = fake
      .to("GET", "/api/v1/notifications")
      .filter((c) => c.url.searchParams.get("unread") === "true");
    expect(unreadCalls).toHaveLength(1);
  });

  it("shows no count when everything is read", async () => {
    hub([escalation(N1, { read_at: NOW_ISO })]);
    show();
    await screen.findAllByTestId("notification");
    expect(screen.queryByTestId("unread-count")).toBeNull();
    expect(bell().getAttribute("aria-label")).toBe("Notifications");
  });

  it("says when there are more unread than one page holds", async () => {
    fakeHub({
      "GET /api/v1/notifications": { items: [escalation(N1)], next_cursor: "more" },
    });
    show();
    expect((await screen.findByTestId("unread-count")).textContent).toBe("1+");
  });

  it("lists escalations with the escalator's reason, or the hub's wording when none was typed", async () => {
    hub([escalation(N1, { reason: "Over my approval limit" }), escalation(N2)]);
    show();
    const [typed, untyped] = await screen.findAllByTestId("notification");
    expect(within(typed!).getByText("Recommendation escalated to you")).toBeTruthy();
    const typedReason = within(typed!).getByTestId("recorded-reason");
    expect(typedReason.dataset.reasonSource).toBe("USER");
    expect(typedReason.textContent).toBe("User reasonOver my approval limit");
    expect(within(untyped!).getByTestId("recorded-reason").textContent).toBe(
      "SystemNo reason was entered.",
    );
    expect(within(typed!).getByRole("link", { name: "Open shortage" }).getAttribute("href")).toBe(
      `/shortages/${shortage.id}`,
    );
  });

  it("hides the shortage link from a user who cannot read shortages", async () => {
    hub([escalation(N1)]);
    show("RECEIVER");
    const [item] = await screen.findAllByTestId("notification");
    expect(within(item!).queryByRole("link")).toBeNull();
  });

  it("marks one read, and the bell's count follows", async () => {
    const fake = hub([escalation(N1), escalation(N2)]);
    show();
    expect((await screen.findByTestId("unread-count")).textContent).toBe("2");
    const [first] = screen.getAllByTestId("notification");
    fireEvent.click(within(first!).getByRole("button", { name: "Mark as read" }));
    await waitFor(() =>
      expect(fake.to("POST", `/api/v1/notifications/${N1}/read`)).toHaveLength(1),
    );
    await waitFor(() => expect(screen.getByTestId("unread-count").textContent).toBe("1"));
    await waitFor(() =>
      expect(
        within(screen.getAllByTestId("notification")[0]!).queryByRole("button", {
          name: "Mark as read",
        }),
      ).toBeNull(),
    );
  });

  it("refreshes the count when the hub reports an escalation (no polling)", async () => {
    let items = [escalation(N1)];
    fakeHub({
      // All of them are unread, so both lists are the same.
      "GET /api/v1/notifications": () => page(items),
    });
    const queryClient = show();
    expect((await screen.findByTestId("unread-count")).textContent).toBe("1");
    items = [escalation(N2), escalation(N1)];
    await act(() =>
      invalidateFor(queryClient, {
        id: "1",
        type: "recommendation.status_changed",
        occurred_at: "2026-10-07T06:06:00Z",
        org_ids: [],
        data: {
          recommendation_id: "r",
          shortage_id: shortage.id,
          from: "PENDING",
          to: "ESCALATED",
        },
      }),
    );
    await waitFor(() => expect(screen.getByTestId("unread-count").textContent).toBe("2"));
    expect(screen.getAllByTestId("notification")).toHaveLength(2);
  });

  it("has an empty state", async () => {
    hub([]);
    show();
    expect(await screen.findByText("No notifications")).toBeTruthy();
  });

  it("shows the hub's error", async () => {
    fakeHub({
      "GET /api/v1/notifications": () => hubError(500, "internal_error", "The hub is down."),
    });
    show();
    expect(await screen.findByText("The hub is down.")).toBeTruthy();
  });
});
