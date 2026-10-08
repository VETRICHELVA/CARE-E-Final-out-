// TanStack Query hooks over the generated client. The hub decides everything (who may move a
// shipment, which vehicle may carry it, the route and ETA); these only fetch, send and refresh.
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, type Schemas, unwrap } from "@care-e/api-client";
import { fetchAllPages, nextCursor, PAGE_LIMIT, reasonBody } from "@care-e/ui";
import { ON_THE_WAY } from "./display";

export type Shipment = Schemas["ShipmentOut"];
export type ShipmentDetail = Schemas["ShipmentDetailOut"];
export type ShipmentStatus = Schemas["ShipmentStatus"];
export type Stop = Schemas["StopOut"];
export type Driver = Schemas["DriverOut"];
export type Vehicle = Schemas["VehicleOut"];
export type Device = Schemas["DeviceOut"];
export type LocationPing = Schemas["LocationOut"];

// Query keys are [path template, params]: `useEventStream()` (on in app.tsx) invalidates them
// by path when the hub reports a change (`shipment.*` refresh the lists and the detail,
// `shipment.location` the detail), so no screen polls.
export const keys = {
  /** Every shipment list (the prefix of the one below). */
  allShipments: ["/api/v1/shipments"] as const,
  shipments: (status: ShipmentStatus | undefined, assignedToMe = false) =>
    ["/api/v1/shipments", { status: status ?? null, assigned_to_me: assignedToMe }] as const,
  /** Shipments this org has on the road (ASSIGNED, PICKED_UP, IN_TRANSIT), for the fleet's
   *  device picker. A key of its own: the lists above are paged. */
  onTheWay: ["/api/v1/shipments", { on_the_way: true }] as const,
  shipment: (id: string) => ["/api/v1/shipments/{shipment_id}", { shipment_id: id }] as const,
  drivers: ["/api/v1/drivers"] as const,
  vehicles: ["/api/v1/vehicles"] as const,
  devices: ["/api/v1/devices"] as const,
};

type Cursor = string | undefined;

/** Shipments the hub shows this org, newest first, a page at a time. `status=CREATED` is the
 *  dispatch board (every logistics org sees unassigned shipments). */
export function useShipments(status: ShipmentStatus | undefined) {
  return useInfiniteQuery({
    queryKey: keys.shipments(status),
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/v1/shipments", {
          params: { query: { status, limit: PAGE_LIMIT, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as Cursor,
    getNextPageParam: nextCursor,
  });
}

/** The caller's own driver jobs (`assigned_to_me=true`), every state. */
export function useMyJobs(enabled = true) {
  return useQuery({
    queryKey: keys.shipments(undefined, true),
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/shipments", {
            params: { query: { assigned_to_me: true, limit: PAGE_LIMIT, cursor } },
          }),
        ),
      ),
    enabled,
  });
}

/** This org's shipments on the way, every page of each of the three states. */
export function useOnTheWay(enabled = true) {
  return useQuery({
    queryKey: keys.onTheWay,
    queryFn: async () =>
      (
        await Promise.all(
          ON_THE_WAY.map((status) =>
            fetchAllPages((cursor) =>
              unwrap(
                client.GET("/api/v1/shipments", {
                  params: { query: { status, limit: PAGE_LIMIT, cursor } },
                }),
              ),
            ),
          ),
        )
      ).flat(),
    enabled,
  });
}

/** One shipment with its route geometry, status history and last driver position. */
export function useShipment(id: string) {
  return useQuery({
    queryKey: keys.shipment(id),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/shipments/{shipment_id}", {
          params: { path: { shipment_id: id } },
        }),
      ),
  });
}

/** This org's drivers (`shipment.assign` only). */
export function useDrivers(enabled = true) {
  return useQuery({
    queryKey: keys.drivers,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(client.GET("/api/v1/drivers", { params: { query: { limit: PAGE_LIMIT, cursor } } })),
      ),
    enabled,
  });
}

/** This org's vehicles (`shipment.assign` only). */
export function useVehicles(enabled = true) {
  return useQuery({
    queryKey: keys.vehicles,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(
          client.GET("/api/v1/vehicles", { params: { query: { limit: PAGE_LIMIT, cursor } } }),
        ),
      ),
    enabled,
  });
}

/** This org's cold boxes with battery, last seen and the shipment each rides with
 *  (`shipment.assign` only). No event reports a new reading's battery or last seen to this
 *  list, so it refetches when the window regains focus (TanStack's default). */
export function useDevices(enabled = true) {
  return useQuery({
    queryKey: keys.devices,
    queryFn: () =>
      fetchAllPages((cursor) =>
        unwrap(client.GET("/api/v1/devices", { params: { query: { limit: PAGE_LIMIT, cursor } } })),
      ),
    enabled,
  });
}

/** Refreshes every shipment view after a change, whatever the hub answered: on a 409 the
 *  shipment has moved on, and the screen should show where it is now. */
function useRefreshShipments() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      [keys.allShipments, ["/api/v1/shipments/{shipment_id}"]].map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ),
    );
}

export type AssignInput = {
  id: string;
  driverId: string;
  vehicleId: string;
  reason: string | undefined;
};

/** CREATED → ASSIGNED with one of this org's drivers and vehicles; the hub computes the route
 *  and ETA, and refuses a vehicle without cold chain for a cold-chain shipment (400). */
export function useAssign() {
  const refresh = useRefreshShipments();
  return useMutation({
    mutationFn: ({ id, driverId, vehicleId, reason }: AssignInput) =>
      unwrap(
        client.POST("/api/v1/shipments/{shipment_id}/assign", {
          params: { path: { shipment_id: id } },
          body: { driver_id: driverId, vehicle_id: vehicleId, ...reasonBody(reason) },
        }),
      ),
    onSettled: refresh,
  });
}

/** ASSIGNED → CREATED, by the carrier org. */
export function useUnassign() {
  const refresh = useRefreshShipments();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string | undefined }) =>
      unwrap(
        client.POST("/api/v1/shipments/{shipment_id}/unassign", {
          params: { path: { shipment_id: id } },
          body: reasonBody(reason),
        }),
      ),
    onSettled: refresh,
  });
}

/** The assigned driver's next step (PICKED_UP, IN_TRANSIT or DELIVERED). */
export function useMoveShipment() {
  const refresh = useRefreshShipments();
  return useMutation({
    mutationFn: ({
      id,
      status,
      reason,
    }: {
      id: string;
      status: ShipmentStatus;
      reason: string | undefined;
    }) =>
      unwrap(
        client.POST("/api/v1/shipments/{shipment_id}/status", {
          params: { path: { shipment_id: id } },
          body: { status, ...reasonBody(reason) },
        }),
      ),
    onSettled: refresh,
  });
}

/** One GPS ping for a shipment. The hub stamps the time and tells the shipment's orgs
 *  (`shipment.location`), which refreshes their detail views. */
export const postLocation = (id: string, lat: number, lng: number) =>
  unwrap(
    client.POST("/api/v1/shipments/{shipment_id}/location", {
      params: { path: { shipment_id: id } },
      body: { lat, lng },
    }),
  );

/** Put a cold box on a shipment, or take it off (`shipmentId: null`). Device changes send no
 *  event, so the device list and the shipment views are refreshed here. */
export function useAssignDevice() {
  const queryClient = useQueryClient();
  const refresh = useRefreshShipments();
  return useMutation({
    mutationFn: ({
      deviceId,
      shipmentId,
      reason,
    }: {
      deviceId: string;
      shipmentId: string | null;
      reason?: string | undefined;
    }) =>
      unwrap(
        client.POST("/api/v1/devices/{device_id}/assign", {
          params: { path: { device_id: deviceId } },
          body: { shipment_id: shipmentId, ...reasonBody(reason) },
        }),
      ),
    onSettled: () =>
      Promise.all([queryClient.invalidateQueries({ queryKey: keys.devices }), refresh()]),
  });
}
