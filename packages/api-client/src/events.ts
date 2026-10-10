// Live updates (api-and-events.md, "Realtime in the apps"): the hub's server-sent events only
// mark TanStack Query caches stale. Event data is never applied as state.
import { type QueryClient, type QueryKey, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { client, unwrap, useAuth } from "./index";

/** The hub's event envelope. */
export type EventEnvelope = {
  id: string;
  type: string;
  occurred_at: string;
  org_ids: string[];
  data: Record<string, unknown>;
};

const API = "/api/v1";
const SHORTAGES = `${API}/shortages`;
const SHORTAGE = `${API}/shortages/{shortage_id}`;
const LATEST_RUN = `${API}/shortages/{shortage_id}/match-runs/latest`;
const LATEST_RECOMMENDATION = `${API}/shortages/{shortage_id}/recommendations/latest`;
const SHORTAGE_AUDIT = `${API}/shortages/{shortage_id}/audit`;
const NOTIFICATIONS = `${API}/notifications`;
const SOURCE_REQUESTS = `${API}/source-requests`;
const BATCHES = `${API}/inventory/batches`;
const OFFERS = `${API}/supplier-offers`;
const NETWORK_DEMAND = `${API}/network/demand`;
const AUDIT = `${API}/audit`;
const RECOMMENDATION = `${API}/recommendations/{recommendation_id}`;
const PURCHASE_ORDERS = `${API}/purchase-orders`;
const SHIPMENTS = `${API}/shipments`;
const SHIPMENT = `${API}/shipments/{shipment_id}`;
const COLDCHAIN = `${API}/shipments/{shipment_id}/coldchain`;
const SURPLUS = `${API}/surplus`;
const SURPLUS_INCOMING = `${API}/surplus/incoming`;
const FORECASTS = `${API}/forecasts`;

const SHORTAGE_VIEWS = [SHORTAGES, SHORTAGE, LATEST_RUN, AUDIT, SHORTAGE_AUDIT] as const;
const RECOMMENDATION_VIEWS = [RECOMMENDATION, LATEST_RECOMMENDATION] as const;
const SHIPMENT_VIEWS = [SHIPMENTS, SHIPMENT, AUDIT, SHORTAGE_AUDIT] as const;
// A reading refreshes the chart only; a cold-chain event also changes the list badges, the
// detail's `inspection_note_required` and the audit trails.
const COLDCHAIN_VIEWS = [SHIPMENT, COLDCHAIN] as const;
const COLDCHAIN_EVENT_VIEWS = [...COLDCHAIN_VIEWS, SHIPMENTS, AUDIT, SHORTAGE_AUDIT] as const;

/**
 * Which queries each event type makes stale. Every query key starts with the API path
 * template it reads (`["/api/v1/shortages/{shortage_id}", {shortage_id}]`); see `isStale`.
 * Paths of screens that do not exist yet follow the spec's endpoint table.
 */
export const EVENT_QUERIES: Record<string, readonly string[]> = {
  "shortage.status_changed": [...SHORTAGE_VIEWS, SOURCE_REQUESTS],
  "source_request.created": [...SHORTAGE_VIEWS, SOURCE_REQUESTS],
  // Accepting or releasing a request places or frees holds, which change `transferable`.
  "source_request.status_changed": [...SHORTAGE_VIEWS, SOURCE_REQUESTS, BATCHES],
  // Stock feeds the forecasts' stock-out dates and expiry risks and what a surplus post offers.
  "inventory.changed": [BATCHES, AUDIT, FORECASTS, SURPLUS],
  // A new or changed offer can add a product to the supplier's network demand.
  "supplier_offer.changed": [OFFERS, NETWORK_DEMAND, AUDIT],
  "recommendation.ready": [...SHORTAGE_VIEWS, ...RECOMMENDATION_VIEWS],
  // An escalation notifies the org's approvers (S12).
  "recommendation.status_changed": [...SHORTAGE_VIEWS, ...RECOMMENDATION_VIEWS, NOTIFICATIONS],
  // An approved BUY leaves open demand; a supplier's rejection sends it back to matching.
  "purchase_order.created": [PURCHASE_ORDERS, SHORTAGE, AUDIT, SHORTAGE_AUDIT, NETWORK_DEMAND],
  "purchase_order.status_changed": [
    PURCHASE_ORDERS,
    SHORTAGE,
    AUDIT,
    SHORTAGE_AUDIT,
    NETWORK_DEMAND,
  ],
  "shipment.created": [...SHIPMENT_VIEWS, SHORTAGE],
  "shipment.status_changed": [...SHIPMENT_VIEWS, SHORTAGE],
  "shipment.location": [SHIPMENT],
  "coldchain.reading": COLDCHAIN_VIEWS,
  "coldchain.excursion": COLDCHAIN_EVENT_VIEWS,
  "coldchain.device_silent": COLDCHAIN_EVENT_VIEWS,
  "coldchain.recovered": COLDCHAIN_EVENT_VIEWS,
  "reconciliation.completed": [...SHORTAGE_VIEWS, ...SHIPMENT_VIEWS, BATCHES],
  "surplus.matched": [SURPLUS, SURPLUS_INCOMING, FORECASTS],
};

/**
 * True if `queryKey` reads one of `paths` and, where its params name a record the event also
 * names (e.g. `shortage_id`), it is the same record. Params the event does not carry match
 * anything, so a list or a differently filtered view is always refreshed.
 */
export function isStale(
  queryKey: QueryKey,
  paths: readonly string[],
  data: Record<string, unknown>,
): boolean {
  const [path, params] = queryKey;
  if (typeof path !== "string" || !paths.includes(path)) return false;
  if (params === null || typeof params !== "object") return true;
  return Object.entries(params).every(([name, value]) => !(name in data) || data[name] === value);
}

/** Invalidates the queries `event` makes stale; unknown event types change nothing. */
export function invalidateFor(queryClient: QueryClient, event: EventEnvelope): Promise<void> {
  const paths = EVENT_QUERIES[event.type];
  if (!paths) return Promise.resolve();
  return queryClient.invalidateQueries({
    predicate: (query) => isStale(query.queryKey, paths, event.data),
  });
}

const listeners = new Set<(event: EventEnvelope) => void>();

/**
 * Calls `listener` with each event the open stream receives, after its queries are marked
 * stale (e.g. to raise a cold-chain alert toast). Returns a function that removes it. The
 * screens still read what they show from the hub, not from the event.
 */
export function onHubEvent(listener: (event: EventEnvelope) => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

type EventSourceLike = Pick<EventSource, "close" | "addEventListener"> & {
  onopen: ((this: EventSource, ev: Event) => unknown) | null;
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null;
  onerror: ((this: EventSource, ev: Event) => unknown) | null;
};

export type StreamOptions = {
  /** Fetches a stream ticket; EventSource cannot send the Authorization header. */
  ticket?: () => Promise<string>;
  /** For tests. */
  EventSource?: new (url: string) => EventSourceLike;
};

/** Reconnect delays: 1 s doubling, capped at 30 s; reset once a connection opens. */
export const backoffMs = (failures: number) => Math.min(1000 * 2 ** failures, 30_000);

const fetchTicket = async () => (await unwrap(client.POST("/api/v1/events/ticket"))).ticket;

/**
 * Opens `/events/stream` and invalidates queries on each event. On any error or when the hub
 * ends the stream (every 15 minutes), reconnects with backoff, a fresh ticket and the last
 * event id, so the hub replays what was missed. Returns a function that stops it.
 */
export function connectEventStream(
  queryClient: QueryClient,
  { ticket = fetchTicket, EventSource: Source = globalThis.EventSource }: StreamOptions = {},
): () => void {
  let stopped = false;
  let failures = 0;
  let lastId: string | undefined;
  let source: EventSourceLike | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const retry = () => {
    if (stopped) return;
    timer = setTimeout(() => void open(), backoffMs(failures));
    failures += 1;
  };

  const open = async () => {
    let value: string;
    try {
      value = await ticket();
    } catch {
      retry();
      return;
    }
    if (stopped) return;
    const query = new URLSearchParams({ ticket: value });
    if (lastId !== undefined) query.set("last_event_id", lastId);
    const es = new Source(`${API}/events/stream?${query}`);
    source = es;
    es.onopen = () => {
      failures = 0;
    };
    es.onmessage = (message) => {
      if (message.lastEventId) lastId = message.lastEventId;
      const event = JSON.parse(message.data as string) as EventEnvelope;
      void invalidateFor(queryClient, event);
      for (const listener of listeners) listener(event);
    };
    // Too much was missed to replay: refetch everything on screen.
    es.addEventListener("reset", () => void queryClient.invalidateQueries());
    es.onerror = () => {
      es.close(); // the browser would retry with the same (by then expired) ticket
      if (source === es) source = undefined;
      retry();
    };
  };

  void open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    source?.close();
  };
}

/**
 * Keeps the signed-in user's queries fresh from the hub's event stream. Call it once, inside
 * the QueryClientProvider. Does nothing while signed out or where EventSource is missing.
 */
export function useEventStream(enabled = true): void {
  const queryClient = useQueryClient();
  const signedIn = useAuth((s) => s.tokens !== null);
  useEffect(() => {
    if (!enabled || !signedIn || typeof globalThis.EventSource === "undefined") return;
    return connectEventStream(queryClient);
  }, [enabled, signedIn, queryClient]);
}
