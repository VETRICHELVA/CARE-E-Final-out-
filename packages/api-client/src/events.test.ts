// @vitest-environment jsdom
import { QueryClient, type QueryKey } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backoffMs, connectEventStream, EVENT_QUERIES, type EventEnvelope } from "./events";

/** A mocked EventSource: records every instance; tests drive its handlers. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: ((ev: Event) => unknown) | null = null;
  onmessage: ((ev: MessageEvent) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  closed = false;
  listeners = new Map<string, () => void>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: () => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
  }
  get params() {
    return new URL(this.url, "http://hub").searchParams;
  }
  open() {
    this.onopen?.(new Event("open"));
  }
  emit(event: Partial<EventEnvelope> & { type: string }, id = "1") {
    const envelope = { id: `e${id}`, occurred_at: "", org_ids: ["o1"], data: {}, ...event };
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(envelope), lastEventId: id }),
    );
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
}

const latest = () => FakeEventSource.instances.at(-1)!;

// Query keys as the apps build them: [path template, params].
const KEYS = {
  me: ["/api/v1/auth/me"],
  products: ["/api/v1/products"],
  batches: ["/api/v1/inventory/batches"],
  offers: ["/api/v1/supplier-offers"],
  shortages: ["/api/v1/shortages"],
  shortage1: ["/api/v1/shortages/{shortage_id}", { shortage_id: "s1" }],
  shortage2: ["/api/v1/shortages/{shortage_id}", { shortage_id: "s2" }],
  run1: ["/api/v1/shortages/{shortage_id}/match-runs/latest", { shortage_id: "s1" }],
  run2: ["/api/v1/shortages/{shortage_id}/match-runs/latest", { shortage_id: "s2" }],
  audit1: ["/api/v1/audit", { entity: "shortage", entity_id: "s1" }],
  incoming: ["/api/v1/source-requests", { direction: "incoming" }],
  outgoing1: ["/api/v1/source-requests", { direction: "outgoing", shortage_id: "s1" }],
  outgoing2: ["/api/v1/source-requests", { direction: "outgoing", shortage_id: "s2" }],
} satisfies Record<string, QueryKey>;
type Name = keyof typeof KEYS;

let queryClient: QueryClient;
let stop: () => void;

function start(ticket = vi.fn(async () => "t-1")) {
  stop = connectEventStream(queryClient, {
    ticket,
    EventSource: FakeEventSource as unknown as NonNullable<
      Parameters<typeof connectEventStream>[1]
    >["EventSource"],
  });
  return ticket;
}

/** The names of the cached queries that are now stale. */
function stale(): Name[] {
  return (Object.keys(KEYS) as Name[]).filter(
    (name) => queryClient.getQueryState(KEYS[name])?.isInvalidated,
  );
}

beforeEach(() => {
  FakeEventSource.instances = [];
  queryClient = new QueryClient();
  for (const key of Object.values(KEYS)) queryClient.setQueryData(key, {});
});
afterEach(() => {
  stop?.();
  vi.useRealTimers();
});

describe("useEventStream's connection", () => {
  it("opens the stream with a ticket", async () => {
    const ticket = start();
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(ticket).toHaveBeenCalledOnce();
    expect(new URL(latest().url, "http://hub").pathname).toBe("/api/v1/events/stream");
    expect(latest().params.get("ticket")).toBe("t-1");
    expect(latest().params.has("last_event_id")).toBe(false);
  });

  it.each<[string, Record<string, unknown>, Name[]]>([
    [
      "shortage.status_changed",
      { shortage_id: "s1", from: "OPEN", to: "MATCHING" },
      ["shortages", "shortage1", "run1", "audit1", "incoming", "outgoing1"],
    ],
    [
      "source_request.created",
      { source_request_id: "r1", shortage_id: "s2", product_id: "p1", qty: 5, deadline: "" },
      ["shortages", "shortage2", "run2", "audit1", "incoming", "outgoing2"],
    ],
    [
      // The hub sends shortage_id, so only that shortage's views (and unkeyed lists) refresh.
      "source_request.status_changed",
      { source_request_id: "r1", shortage_id: "s1", from: "REQUESTED", to: "TENTATIVE_HOLD" },
      ["batches", "shortages", "shortage1", "run1", "audit1", "incoming", "outgoing1"],
    ],
    [
      // An event without shortage_id refreshes every shortage view and request list.
      "source_request.status_changed",
      { source_request_id: "r1", from: "REQUESTED", to: "TENTATIVE_HOLD" },
      [
        "batches",
        "shortages",
        "shortage1",
        "shortage2",
        "run1",
        "run2",
        "audit1",
        "incoming",
        "outgoing1",
        "outgoing2",
      ],
    ],
    ["inventory.changed", { batch_ids: ["b1"], product_ids: ["p1"] }, ["batches", "audit1"]],
    ["supplier_offer.changed", { offer_id: "o1", product_id: "p1" }, ["offers", "audit1"]],
    ["coldchain.reading", { shipment_id: "x", temp_c: 4, ts: "" }, []],
    ["not.a.real.event", {}, []],
  ])("on %s invalidates exactly the right query keys", async (type, data, expected) => {
    start();
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    latest().emit({ type, data });
    await vi.waitFor(() => expect(stale().sort()).toEqual([...expected].sort()));
    expect(stale()).not.toContain("me");
    expect(stale()).not.toContain("products");
  });

  it("maps every event type in the spec", () => {
    const spec = [
      "shortage.status_changed",
      "source_request.created",
      "source_request.status_changed",
      "recommendation.ready",
      "purchase_order.created",
      "purchase_order.status_changed",
      "shipment.created",
      "shipment.status_changed",
      "shipment.location",
      "coldchain.reading",
      "coldchain.excursion",
      "coldchain.device_silent",
      "coldchain.recovered",
      "reconciliation.completed",
      "surplus.matched",
      "inventory.changed",
      "supplier_offer.changed",
    ];
    expect(Object.keys(EVENT_QUERIES).sort()).toEqual(spec.sort());
  });

  it("refetches everything on reset", async () => {
    start();
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    latest().listeners.get("reset")!();
    await vi.waitFor(() => expect(stale()).toHaveLength(Object.keys(KEYS).length));
  });

  it("reconnects with backoff, a new ticket and the last event id", async () => {
    vi.useFakeTimers();
    let n = 0;
    const ticket = start(vi.fn(async () => `t-${++n}`));
    await vi.advanceTimersByTimeAsync(0);
    const first = latest();
    first.open();
    first.emit({ type: "inventory.changed" }, "41");
    first.emit({ type: "inventory.changed" }, "42");

    first.fail();
    expect(first.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeEventSource.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    const second = latest();
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(second.params.get("ticket")).toBe("t-2");
    expect(second.params.get("last_event_id")).toBe("42");

    // It fails again before opening: the next wait doubles.
    second.fail();
    await vi.advanceTimersByTimeAsync(1999);
    expect(FakeEventSource.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.instances).toHaveLength(3);
    expect(ticket).toHaveBeenCalledTimes(3);

    // Once a connection opens, the backoff starts again from 1 s.
    latest().open();
    latest().fail();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.instances).toHaveLength(4);
  });

  it("retries when the ticket cannot be fetched, and stops for good when stopped", async () => {
    vi.useFakeTimers();
    const ticket = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue("t-ok");
    start(ticket);
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.instances).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.instances).toHaveLength(1);

    const source = latest();
    stop();
    expect(source.closed).toBe(true);
    source.fail();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("backs off 1 s doubling, capped at 30 s", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000,
    ]);
  });
});
