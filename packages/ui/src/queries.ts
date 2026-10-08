// Query helpers shared by the apps (moved from hospital-web in S10). Query keys start with the
// API path template, so `useEventStream()` can invalidate them (api-and-events.md, Realtime).
import { useQuery } from "@tanstack/react-query";
import { client, type Schemas, unwrap } from "@care-e/api-client";

export type Product = Schemas["ProductOut"];

/** The hub's maximum `limit`. */
export const PAGE_LIMIT = 200;

/** `getNextPageParam` for a hub page: `{items, next_cursor}`. */
export const nextCursor = (page: { next_cursor?: string | null }) => page.next_cursor ?? undefined;

/** No reason typed → no body, and the hub records "No reason was entered." itself. */
export const reasonBody = (reason: string | undefined) => (reason ? { reason } : undefined);

/** Fetches every page of a hub list. For small lists only (the catalog, an org's offers). */
export async function fetchAllPages<T>(
  get: (cursor: string | undefined) => Promise<{ items: T[]; next_cursor?: string | null }>,
): Promise<T[]> {
  const all: T[] = [];
  let cursor: string | undefined;
  do {
    const page = await get(cursor);
    all.push(...page.items);
    cursor = nextCursor(page);
  } while (cursor);
  return all;
}

export const productsKey = ["/api/v1/products"] as const;

/** The whole catalog (40 products), keyed by id: most records carry only `product_id`. */
export function useProducts() {
  return useQuery({
    queryKey: productsKey,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/products", { params: { query: { limit: PAGE_LIMIT, cursor } } }),
        ),
      ),
    select: (products: Product[]) => ({
      list: products,
      byId: new Map(products.map((p) => [p.id, p])),
    }),
    staleTime: 5 * 60_000,
  });
}
