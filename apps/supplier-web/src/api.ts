// TanStack Query hooks over the generated client. The hub decides everything; these only
// fetch, send and refresh. Never compute a hub figure (demand totals, eligibility) here.
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, client, type Schemas, unwrap } from "@care-e/api-client";
import { fetchAllPages, nextCursor, PAGE_LIMIT, reasonBody } from "@care-e/ui";

export { useProducts } from "@care-e/ui";
export type { Product } from "@care-e/ui";
export type Offer = Schemas["OfferOut"];
export type OfferIn = Schemas["OfferIn"];
export type PurchaseOrder = Schemas["PurchaseOrderOut"];
export type PoStatus = Schemas["PoStatus"];
export type Demand = Schemas["DemandOut"];
export type PoAction = "acknowledge" | "reject" | "dispatch";

// Query keys are [path template, params]: `useEventStream()` (on in app.tsx) invalidates
// them by path when the hub reports a change, so no screen polls.
export const keys = {
  offers: ["/api/v1/supplier-offers"] as const,
  demand: ["/api/v1/network/demand"] as const,
  /** Every purchase-order view (the prefix of the two below). */
  allOrders: ["/api/v1/purchase-orders"] as const,
  orders: (status: PoStatus | undefined) =>
    ["/api/v1/purchase-orders", { status: status ?? null }] as const,
  /** One order. There is no GET by id; the key names the order, so only its own events
   *  (`purchase_order_id`) refresh it. */
  order: (id: string) => ["/api/v1/purchase-orders", { purchase_order_id: id }] as const,
};

type Cursor = string | undefined;

/** Own org's offers (one per product), oldest first. */
export function useOffers() {
  return useQuery({
    queryKey: keys.offers,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/supplier-offers", {
            params: { query: { limit: PAGE_LIMIT, cursor } },
          }),
        ),
      ),
    select: (offers: Offer[]) => ({
      list: offers,
      byProduct: new Map(offers.map((o) => [o.product_id, o])),
    }),
  });
}

/** Open shortfall totals for each product this org offers; the hub hides who is short. */
export function useDemand(enabled = true) {
  return useQuery({
    queryKey: keys.demand,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/network/demand", {
            params: { query: { limit: PAGE_LIMIT, cursor } },
          }),
        ),
      ),
    enabled,
  });
}

/** Orders sent to this org, newest first, a page at a time; optionally one status. */
export function usePurchaseOrders(status?: PoStatus, enabled = true) {
  return useInfiniteQuery({
    queryKey: keys.orders(status),
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/purchase-orders", {
          params: { query: { status, limit: PAGE_LIMIT, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
    enabled,
  });
}

/** One order, found by paging the org's list (the hub has no GET by id); 404 when absent. */
export function usePurchaseOrder(id: string) {
  return useQuery({
    queryKey: keys.order(id),
    queryFn: async () => {
      let cursor: Cursor;
      do {
        const page = await unwrap(
          client.GET("/api/v1/purchase-orders", {
            params: { query: { limit: PAGE_LIMIT, cursor } },
          }),
        );
        const found = page.items.find((po) => po.id === id);
        if (found) return found;
        cursor = nextCursor(page);
      } while (cursor);
      throw new ApiError(404, "not_found", "Purchase order not found.");
    },
  });
}

/** Create or update this org's offer for one product; the hub stamps `updated_at`. */
export function usePutOffer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferIn) => unwrap(client.PUT("/api/v1/supplier-offers", { body })),
    onSuccess: () =>
      Promise.all(
        [keys.offers, keys.demand].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ),
  });
}

/** Acknowledge, reject or dispatch. Whatever the hub answers, the order views are refetched:
 *  on a 409 the order has moved on, and the screen should show where it is now. */
export function usePoAction(action: PoAction) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string | undefined }) =>
      unwrap(
        client.POST(`/api/v1/purchase-orders/{po_id}/${action}`, {
          params: { path: { po_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.allOrders }),
  });
}
