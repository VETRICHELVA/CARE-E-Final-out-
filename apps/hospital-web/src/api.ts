// TanStack Query hooks over the generated client. The hub decides everything; these only
// fetch, send and refresh. Never compute a hub figure (shortfall, transferable, rank) here.
import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiError, client, type Schemas, unwrap } from "@care-e/api-client";
import {
  fetchAllPages,
  nextCursor,
  PAGE_LIMIT as PAGE,
  productsKey,
  reasonBody,
  useMe,
} from "@care-e/ui";

export { useProducts } from "@care-e/ui";
export type { Product } from "@care-e/ui";
export type Batch = Schemas["BatchOut"];
export type Facility = Schemas["FacilityOut"];
export type Shortage = Schemas["ShortageOut"];
export type MatchRun = Schemas["MatchRunOut"];
export type Candidate = Schemas["CandidateOut"];
export type AuditRow = Schemas["AuditOut"];
export type SourceRequest = Schemas["SourceRequestOut"];
export type Recommendation = Schemas["RecommendationOut"];
export type RecommendationLine = Schemas["RecommendationLineOut"];
export type Approval = Schemas["ApprovalOut"];
export type Shipment = Schemas["ShipmentOut"];
export type ShipmentDetail = Schemas["ShipmentDetailOut"];
export type Receipt = Schemas["ReceiptOut"];
export type ReceiptIn = Schemas["ReceiptIn"];
export type Reconciliation = Schemas["ReconciliationOut"];
export type Notification = Schemas["NotificationOut"];
export type Forecast = Schemas["ForecastOut"];
export type ExpiryRisk = Schemas["ExpiryRiskOut"];
export type SurplusPost = Schemas["SurplusOut"];
export type SurplusOffer = Schemas["SurplusOfferOut"];
export type Reliability = Schemas["ReliabilityOut"];
type SourceRequestQuery = {
  direction: Schemas["Direction"];
  status?: Schemas["RequestStatus"];
  shortage_id?: string;
};

// Query keys are [path template, params]: `useEventStream()` (on in app.tsx) invalidates
// them by path when the hub reports a change, so no screen polls.
export const keys = {
  batches: ["/api/v1/inventory/batches"] as const,
  products: productsKey,
  facilities: (orgId: string) => ["/api/v1/orgs/{org_id}/facilities", { org_id: orgId }] as const,
  shortages: ["/api/v1/shortages"] as const,
  shortage: (id: string) => ["/api/v1/shortages/{shortage_id}", { shortage_id: id }] as const,
  latestRun: (id: string) =>
    ["/api/v1/shortages/{shortage_id}/match-runs/latest", { shortage_id: id }] as const,
  latestRecommendation: (id: string) =>
    ["/api/v1/shortages/{shortage_id}/recommendations/latest", { shortage_id: id }] as const,
  shortageAudit: (id: string) =>
    ["/api/v1/shortages/{shortage_id}/audit", { shortage_id: id }] as const,
  audit: (entity: string, id: string) => ["/api/v1/audit", { entity, entity_id: id }] as const,
  allAudit: ["/api/v1/audit"] as const,
  recommendation: (id: string) =>
    ["/api/v1/recommendations/{recommendation_id}", { recommendation_id: id }] as const,
  /** Every shipment list (the prefix of `inbound`). */
  shipments: ["/api/v1/shipments"] as const,
  inbound: ["/api/v1/shipments", { direction: "inbound" }] as const,
  shipment: (id: string) => ["/api/v1/shipments/{shipment_id}", { shipment_id: id }] as const,
  /** Every source-request list (the prefix of `sourceRequests`). */
  allSourceRequests: ["/api/v1/source-requests"] as const,
  sourceRequests: (query: SourceRequestQuery) => ["/api/v1/source-requests", query] as const,
  /** Every notification list (the prefix of the two below). */
  notifications: ["/api/v1/notifications"] as const,
  allNotifications: ["/api/v1/notifications", { unread: false }] as const,
  unreadNotifications: ["/api/v1/notifications", { unread: true }] as const,
  forecasts: ["/api/v1/forecasts"] as const,
  surplus: ["/api/v1/surplus"] as const,
  incomingSurplus: ["/api/v1/surplus/incoming"] as const,
  reliability: (orgId: string) => ["/api/v1/orgs/{org_id}/reliability", { org_id: orgId }] as const,
};

type Cursor = string | undefined;

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

/** An org's stored reliability score and its four components (business-rules.md §12, S19);
 *  `credits` only for the caller's own org. Fetched only while `enabled`. */
export function useReliability(orgId: string, enabled = true) {
  return useQuery({
    queryKey: keys.reliability(orgId),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/orgs/{org_id}/reliability", { params: { path: { org_id: orgId } } }),
      ),
    enabled: enabled && orgId !== "",
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
    // `source` and `status` have hub defaults (FORM, OPEN), which the generated type marks as
    // required; the form leaves them out, a chat card sends CHAT and OPEN or DRAFT (S17).
    mutationFn: (
      body: Omit<Schemas["ShortageCreate"], "source" | "status"> &
        Partial<Pick<Schemas["ShortageCreate"], "source" | "status">>,
    ) => unwrap(client.POST("/api/v1/shortages", { body: body as Schemas["ShortageCreate"] })),
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
        keys.shortageAudit(id),
        keys.latestRecommendation(id),
        keys.allSourceRequests,
      ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
    );
}

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

/** DRAFT -> OPEN (S17: the requester confirms a saved chat draft); the hub then matches it. */
export function useConfirmDraft(id: string) {
  const refresh = useRefreshShortage(id);
  return useMutation({
    mutationFn: (reason: string | undefined) =>
      unwrap(
        client.POST("/api/v1/shortages/{shortage_id}/confirm", {
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

// ---- The shortage's recommendation and audit trail (S12) ----

/** The shortage's newest recommendation (the one awaiting a decision while there is one, else
 *  the last decided or expired), or null before the hub has made one (its 404). Any user of the
 *  shortage's org may read it. Seeds the recommendation's own query, which decisions update. */
export function useLatestRecommendation(shortageId: string) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: keys.latestRecommendation(shortageId),
    queryFn: async () => {
      try {
        const rec = await unwrap(
          client.GET("/api/v1/shortages/{shortage_id}/recommendations/latest", {
            params: { path: { shortage_id: shortageId } },
          }),
        );
        queryClient.setQueryData(keys.recommendation(rec.id), rec);
        return rec;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

/** The id of the shortage's current recommendation, or null while it has none. */
export function useCurrentRecommendationId(shortageId: string) {
  const latest = useLatestRecommendation(shortageId);
  return { id: latest.data?.id ?? null, isPending: latest.isPending, error: latest.error };
}

/** The shortage's whole trail in this org (`audit.read`): the shortage and every record made
 *  for it, newest first. The hub pages it oldest first; a trail is short, so every page is read. */
export function useShortageAudit(shortageId: string, enabled = true) {
  return useQuery({
    queryKey: keys.shortageAudit(shortageId),
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/shortages/{shortage_id}/audit", {
            params: { path: { shortage_id: shortageId }, query: { limit: PAGE, cursor } },
          }),
        ),
      ),
    select: (rows: AuditRow[]) => [...rows].reverse(),
    enabled,
  });
}

// ---- Recommendations (S09): the decision panel ----

/** One recommendation, as the requester's org sees it (hospital costs are null by design). */
export function useRecommendation(id: string | null) {
  return useQuery({
    queryKey: keys.recommendation(id ?? ""),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/recommendations/{recommendation_id}", {
          params: { path: { recommendation_id: id ?? "" } },
        }),
      ),
    enabled: id !== null,
  });
}

export type Decision = "approve" | "reject" | "escalate";

/** Approve, reject or escalate (`recommendation.approve`). Approve returns the hub's
 *  business-rules §13 `message`. Whatever the hub answers, everything the decision touches is
 *  refetched: after a 409 the recommendation has moved on (expired, already decided). */
export function useDecide(rec: Recommendation) {
  const queryClient = useQueryClient();
  const refresh = useRefreshShortage(rec.shortage_id);
  return useMutation({
    mutationFn: async ({
      decision,
      reason,
    }: {
      decision: Decision;
      reason: string | undefined;
    }): Promise<{ recommendation: Recommendation; message: string | null }> => {
      const request = {
        params: { path: { recommendation_id: rec.id } },
        body: reasonBody(reason),
      };
      if (decision === "approve")
        return unwrap(client.POST("/api/v1/recommendations/{recommendation_id}/approve", request));
      const recommendation = await unwrap(
        client.POST(`/api/v1/recommendations/{recommendation_id}/${decision}`, request),
      );
      return { recommendation, message: null };
    },
    onSuccess: ({ recommendation }) =>
      queryClient.setQueryData(keys.recommendation(rec.id), recommendation),
    onSettled: () =>
      Promise.all([
        refresh(),
        ...[keys.recommendation(rec.id), keys.allAudit, keys.shipments].map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      ]),
  });
}

// ---- Shipments (S11, S12): deliveries and receiving ----

/** Shipments to this org (its deliveries), newest first, a page at a time. */
export function useInboundShipments() {
  return useInfiniteQuery({
    queryKey: keys.inbound,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/shipments", {
          params: { query: { direction: "inbound", limit: PAGE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
  });
}

/** One shipment: `qty` is what is expected, `inspection_note_required` and, once recorded,
 *  `receipt` (with its reconciliation) for the receiving org. */
export function useShipment(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.shipment(id),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/shipments/{shipment_id}", { params: { path: { shipment_id: id } } }),
      ),
    enabled,
  });
}

/** Several shipments by id (the audit tab names who moved each one). */
export function useShipmentDetails(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: keys.shipment(id),
      queryFn: () =>
        unwrap(
          client.GET("/api/v1/shipments/{shipment_id}", {
            params: { path: { shipment_id: id } },
          }),
        ),
    })),
    combine: (results) => results.flatMap((r) => (r.data ? [r.data] : [])),
  });
}

/** Records what arrived (`receipt.record`, the receiving org). The hub checks the figures,
 *  moves the shipment to RECONCILED, adds the accepted stock as a batch and, once every
 *  shipment of the shortage has a receipt, reconciles it. Whatever it answers, everything the
 *  receipt can change is refetched. */
export function useRecordReceipt(shipment: Pick<Shipment, "id" | "shortage_id">) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReceiptIn) =>
      unwrap(
        client.POST("/api/v1/shipments/{shipment_id}/receipt", {
          params: { path: { shipment_id: shipment.id } },
          body,
        }),
      ),
    onSettled: () =>
      Promise.all(
        [
          keys.shipment(shipment.id),
          keys.shipments,
          keys.batches,
          keys.shortages,
          keys.shortage(shipment.shortage_id),
          keys.shortageAudit(shipment.shortage_id),
        ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ),
  });
}

// ---- Notifications (S12): the caller's own ----

/** The caller's notifications, newest first, a page at a time. */
export function useNotifications() {
  return useInfiniteQuery({
    queryKey: keys.allNotifications,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/notifications", {
          params: { query: { limit: PAGE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
  });
}

/** How many of the caller's notifications are unread, from the hub's first page of unread
 *  ones; `more` when there are more than a page holds. */
export function useUnreadCount() {
  return useQuery({
    queryKey: keys.unreadNotifications,
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/notifications", { params: { query: { unread: true, limit: PAGE } } }),
      ),
    select: (page) => ({ count: page.items.length, more: Boolean(page.next_cursor) }),
  });
}

export function useMarkRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        client.POST("/api/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: id } },
        }),
      ),
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.notifications }),
  });
}

// ---- Forecasts and surplus (S18) ----

/** Own org's forecasts, one per product (an org has at most the catalog's products, so every
 *  page is read). Every figure is the hub's: stock-out dates, reorder and expiry-risk excess. */
export function useForecasts(enabled = true) {
  return useQuery({
    queryKey: keys.forecasts,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(client.GET("/api/v1/forecasts", { params: { query: { limit: PAGE, cursor } } })),
      ),
    enabled,
  });
}

/** Forecast now instead of waiting for the nightly run (`inventory.edit`, own org). */
export function useRunForecasts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(client.POST("/api/v1/forecasts/run")),
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.forecasts }),
  });
}

/** Own org's surplus posts, newest first, a page at a time (`inventory.edit`). */
export function useSurplusPosts(enabled = true) {
  return useInfiniteQuery({
    queryKey: keys.surplus,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/surplus", { params: { query: { limit: PAGE, cursor: pageParam } } }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
    enabled,
  });
}

/** Other hospitals' surplus matched to this org: offered qty, expiry band and location only. */
export function useIncomingSurplus(enabled = true) {
  return useQuery({
    queryKey: keys.incomingSurplus,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/surplus/incoming", { params: { query: { limit: PAGE, cursor } } }),
        ),
      ),
    enabled,
  });
}

function useRefreshSurplus() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      [keys.surplus, keys.forecasts, keys.incomingSurplus].map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ),
    );
}

/** Offers a batch to the network. The hub refuses more than the batch's transferable. */
export function useCreateSurplus() {
  const refresh = useRefreshSurplus();
  return useMutation({
    mutationFn: (body: Schemas["SurplusCreate"]) =>
      unwrap(client.POST("/api/v1/surplus", { body })),
    onSettled: refresh,
  });
}

export function useWithdrawSurplus() {
  const refresh = useRefreshSurplus();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string | undefined }) =>
      unwrap(
        client.POST("/api/v1/surplus/{surplus_id}/withdraw", {
          params: { path: { surplus_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSettled: refresh,
  });
}
