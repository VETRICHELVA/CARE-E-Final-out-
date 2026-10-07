// TanStack Query hooks over the generated client. The hub decides everything; these only
// fetch, send and refresh. Never compute a hub figure (shortfall, transferable, rank) here.
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, client, type Schemas, unwrap } from "@care-e/api-client";
import { useMe } from "@care-e/ui";

export type Batch = Schemas["BatchOut"];
export type Product = Schemas["ProductOut"];
export type Facility = Schemas["FacilityOut"];
export type Shortage = Schemas["ShortageOut"];
export type MatchRun = Schemas["MatchRunOut"];
export type Candidate = Schemas["CandidateOut"];
export type AuditRow = Schemas["AuditOut"];

/** Until S07's `useEventStream()` lands, screens poll the hub this often. */
export const POLL_MS = 10_000;
const PAGE = 200; // the hub's maximum `limit`

// Query keys are [path template, params]: S07's event handler can invalidate by path.
export const keys = {
  batches: ["/api/v1/inventory/batches"] as const,
  products: ["/api/v1/products"] as const,
  facilities: (orgId: string) => ["/api/v1/orgs/{org_id}/facilities", { org_id: orgId }] as const,
  shortages: ["/api/v1/shortages"] as const,
  shortage: (id: string) => ["/api/v1/shortages/{shortage_id}", { shortage_id: id }] as const,
  latestRun: (id: string) =>
    ["/api/v1/shortages/{shortage_id}/match-runs/latest", { shortage_id: id }] as const,
  audit: (entity: string, id: string) => ["/api/v1/audit", { entity, entity_id: id }] as const,
};

type Cursor = string | undefined;
const nextCursor = (page: { next_cursor?: string | null }) => page.next_cursor ?? undefined;

/** Own org's batches, oldest first, a page at a time. */
export function useBatches() {
  return useInfiniteQuery({
    queryKey: keys.batches,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/inventory/batches", {
          params: { query: { limit: PAGE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
    refetchInterval: POLL_MS,
  });
}

/** Own org's shortages, newest first, a page at a time. */
export function useShortages(enabled = true) {
  return useInfiniteQuery({
    queryKey: keys.shortages,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/shortages", { params: { query: { limit: PAGE, cursor: pageParam } } }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
    refetchInterval: POLL_MS,
    enabled,
  });
}

/** The whole catalog (40 products), keyed by id: batches and shortages carry only `product_id`. */
export function useProducts() {
  return useQuery({
    queryKey: keys.products,
    queryFn: async () => {
      const all: Product[] = [];
      let cursor: Cursor;
      do {
        const page = await unwrap(
          client.GET("/api/v1/products", { params: { query: { limit: PAGE, cursor } } }),
        );
        all.push(...page.items);
        cursor = nextCursor(page);
      } while (cursor);
      return all;
    },
    select: (products) => ({ list: products, byId: new Map(products.map((p) => [p.id, p])) }),
    staleTime: 5 * 60_000,
  });
}

/** The signed-in user's own facilities. */
export function useFacilities() {
  const orgId = useMe().data?.org.id ?? "";
  return useQuery({
    queryKey: keys.facilities(orgId),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/orgs/{org_id}/facilities", {
          params: { path: { org_id: orgId }, query: { limit: PAGE } },
        }),
      ),
    // Own org → full FacilityOut; the public view (other orgs) has no id.
    select: (page): Facility[] =>
      (page.items as (Facility | Schemas["PublicFacilityView"])[]).filter(
        (f): f is Facility => "id" in f,
      ),
    enabled: orgId !== "",
    staleTime: 5 * 60_000,
  });
}

export function useShortage(id: string) {
  return useQuery({
    queryKey: keys.shortage(id),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/shortages/{shortage_id}", { params: { path: { shortage_id: id } } }),
      ),
    refetchInterval: POLL_MS,
  });
}

/** The latest match run, or null while the shortage has none (the hub's 404). */
export function useLatestRun(id: string) {
  return useQuery({
    queryKey: keys.latestRun(id),
    queryFn: async () => {
      try {
        return await unwrap(
          client.GET("/api/v1/shortages/{shortage_id}/match-runs/latest", {
            params: { path: { shortage_id: id } },
          }),
        );
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    refetchInterval: POLL_MS,
  });
}

/** A record's audit rows, newest first; only for users with `audit.read`. */
export function useAudit(entity: string, id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.audit(entity, id),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/audit", {
          params: { query: { entity, entity_id: id, limit: PAGE } },
        }),
      ),
    enabled,
    refetchInterval: POLL_MS,
  });
}

// ---- Inventory writes (`inventory.edit`, HOSPITAL orgs) ----

export function useUpdateBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["BatchUpdate"] }) =>
      unwrap(
        client.PATCH("/api/v1/inventory/batches/{batch_id}", {
          params: { path: { batch_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.batches }),
  });
}

export function useVerifyBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["VerifyIn"] }) =>
      unwrap(
        client.POST("/api/v1/inventory/batches/{batch_id}/verify", {
          params: { path: { batch_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.batches }),
  });
}

/** Sends the file as the raw `text/csv` body the hub reads (not JSON). */
export function useImportBatches() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      facilityId,
      file,
      reason,
    }: {
      facilityId: string;
      file: Blob;
      reason?: string;
    }) =>
      unwrap(
        client.POST("/api/v1/inventory/batches/import", {
          params: { query: { facility_id: facilityId, reason } },
          body: await file.text(),
          bodySerializer: (csv: string) => csv,
          headers: { "Content-Type": "text/csv" },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.batches }),
  });
}

// ---- Shortage writes (`shortage.create`) ----

export function useCreateShortage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ShortageCreate"]) =>
      unwrap(client.POST("/api/v1/shortages", { body })),
    onSuccess: (shortage) => {
      queryClient.setQueryData(keys.shortage(shortage.id), shortage);
      return queryClient.invalidateQueries({ queryKey: keys.shortages });
    },
  });
}

/** Everything a shortage action can change. */
function useRefreshShortage(id: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      [keys.shortage(id), keys.latestRun(id), keys.shortages, keys.audit("shortage", id)].map(
        (queryKey) => queryClient.invalidateQueries({ queryKey }),
      ),
    );
}

/** No reason typed → no body, and the hub records "No reason was entered." itself. */
const reasonBody = (reason: string | undefined) => (reason ? { reason } : undefined);

export function useCancelShortage(id: string) {
  const refresh = useRefreshShortage(id);
  return useMutation({
    mutationFn: (reason: string | undefined) =>
      unwrap(
        client.POST("/api/v1/shortages/{shortage_id}/cancel", {
          params: { path: { shortage_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSuccess: refresh,
  });
}

export function useRerunMatch(id: string) {
  const refresh = useRefreshShortage(id);
  return useMutation({
    mutationFn: (reason: string | undefined) =>
      unwrap(
        client.POST("/api/v1/shortages/{shortage_id}/match", {
          params: { path: { shortage_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSuccess: refresh,
  });
}
