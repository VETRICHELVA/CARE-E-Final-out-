// Labels and display gates. Display only: the hub enforces every rule and still checks each call.
import { formatQty, type Product } from "@care-e/ui";
import type { Shipment, ShipmentStatus, Vehicle } from "./api";

/** api-and-events.md (S11, S14): assigning, the fleet and devices need `shipment.assign`
 *  (DISPATCHER); status steps and location pings need `shipment.update_status` (DRIVER). */
export const SHIPMENT_ASSIGN = "shipment.assign";
export const SHIPMENT_UPDATE_STATUS = "shipment.update_status";

/** business-rules.md §8, Shipment: the one step the assigned driver may take from each state
 *  (ASSIGNED → PICKED_UP → IN_TRANSIT → DELIVERED). Used only to show the single button the
 *  hub would accept; it answers 409 to anything else. */
export const DRIVER_NEXT: Partial<Record<ShipmentStatus, ShipmentStatus>> = {
  ASSIGNED: "PICKED_UP",
  PICKED_UP: "IN_TRANSIT",
  IN_TRANSIT: "DELIVERED",
};
export const nextStep = (status: ShipmentStatus) => DRIVER_NEXT[status];

/** The driver's button for each step. */
export const STEP_LABEL: Partial<Record<ShipmentStatus, string>> = {
  PICKED_UP: "Picked up",
  IN_TRANSIT: "In transit",
  DELIVERED: "Delivered",
};

/** States in which the hub takes location pings (and links cold-box readings). */
export const ON_THE_WAY: readonly ShipmentStatus[] = ["ASSIGNED", "PICKED_UP", "IN_TRANSIT"];
export const isOnTheWay = (status: ShipmentStatus) => ON_THE_WAY.includes(status);

/** A device can no longer be attached once the stock has arrived (409 from the hub). */
export const ARRIVED: readonly ShipmentStatus[] = ["DELIVERED", "RECONCILED"];

/** Dispatch board filters, in the order the shipment moves. */
export const SHIPMENT_STATUSES: readonly ShipmentStatus[] = [
  "CREATED",
  "ASSIGNED",
  "PICKED_UP",
  "IN_TRANSIT",
  "DELIVERED",
  "RECONCILED",
];

/** The vehicles worth offering: a cold-chain shipment only on a cold-chain vehicle. The hub
 *  checks again on assign (400 `cold_chain_vehicle_required`). */
export const vehiclesFor = (
  shipment: Pick<Shipment, "requires_cold_chain">,
  vehicles: Vehicle[],
) => (shipment.requires_cold_chain ? vehicles.filter((v) => v.has_cold_chain) : vehicles);

/** A pickup or drop: the org's name, then the stop's place when the hub has one. */
export const place = (org: string, stop: { place: string } | null) =>
  stop && stop.place && stop.place !== org ? `${org}, ${stop.place}` : org;

/** A quantity in the product's unit; "units" while the catalog is loading. */
export const qty = (n: number, product: Product | undefined) =>
  formatQty(n, product?.unit ?? "units");

const km = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
export const formatKm = (distance: number) => `${km.format(distance)} km`;

/** How the hub got the route (business-rules §4): the road network, or the straight-line
 *  fallback when OSRM was off, failing or slow. */
export const ROUTE_SOURCE: Record<string, string> = {
  OSRM: "road route",
  HAVERSINE: "straight-line estimate",
};

/** "12 min ago", "3 h ago", "2 d ago"; "just now" under a minute. For device last seen. */
export function ago(iso: string, now: number): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/** Map tiles: OpenStreetMap's standard tiles unless `VITE_MAP_TILE_URL` names another server
 *  (e.g. a self-hosted tile cache for the demo). */
export const TILE_URL =
  (import.meta.env.VITE_MAP_TILE_URL as string | undefined) ||
  "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const TILE_ATTRIBUTION =
  (import.meta.env.VITE_MAP_TILE_ATTRIBUTION as string | undefined) ||
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** How often the driver's phone sends its position (apps-ai-iot.md, Driver jobs). */
export const PING_INTERVAL_MS = 30_000;
