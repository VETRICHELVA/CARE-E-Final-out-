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
export type SourceRequest = Schemas["SourceRequestOut"];
type SourceRequestQuery = {
  direction: Schemas["Direction"];
  status?: Schemas["RequestStatus"];
  shortage_id?: string;
};

const PAGE = 200; // the hub's maximum `limit`

// Query keys are [path template, params]: `useEventStream()` (on in app.tsx) invalidates
// them by path when the hub reports a change, so no screen polls.
export const keys = {
  batches: ["/api/v1/inventory/batches"] as const,
  products: ["/api/v1/products"] as const,
  facilities: (orgId: string) => ["/api/v1/orgs/{org_id}/facilities", { org_id: orgId }] as const,
  shortages: ["/api/v1/shortages"] as const,
  shortage: (id: string) => ["/api/v1/shortages/{shortage_id}", { shortage_id: id }] as const,
  latestRun: (id: string) =>
    ["/api/v1/shortages/{shortage_id}/match-runs/latest", { shortage_id: id }] as const,
  audit: (entity: string, id: string) => ["/api/v1/audit", { entity, entity_id: id }] as const,
  /** Every source-request list (the prefix of `sourceRequests`). */
  allSourceRequests: ["/api/v1/source-requests"] as const,
  sourceRequests: (query: SourceRequestQuery) => ["/api/v1/source-requests", query] as const,
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
  });
}

/** Source requests to this org (`incoming`) or for its shortages (`outgoing`), newest first. */
export function useSourceRequests(query: SourceRequestQuery, enabled = true) {
  return useInfiniteQuery({
    queryKey: keys.sourceRequests(query),
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/source-requests", {
          params: { query: { ...query, limit: PAGE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
    enabled,
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
      [
        keys.shortage(id),
        keys.latestRun(id),
        keys.shortages,
        keys.audit("shortage", id),
        keys.allSourceRequests,
      ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
    );
}

/** No reason typed → no body, and the hub records "No reason was entered." itself. */
const reasonBody = (reason: string | undefined) => (reason ? { reason } : undefined);

// ---- Source request answers (`source_request.respond`, source org only) ----

/** Accept (places tentative holds) or decline (matching re-runs without this org). Whatever the
 *  hub answers, the lists are refetched: on a 409 the request has moved on (expired, already
 *  answered), and the screen should show where it is now. */
export function useRespondToRequest(answer: "accept" | "decline") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string | undefined }) =>
      unwrap(
        client.POST(`/api/v1/source-requests/{request_id}/${answer}`, {
          params: { path: { request_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSettled: () =>
      Promise.all(
        [keys.allSourceRequests, keys.batches].map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      ),
  });
}

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
