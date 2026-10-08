// The shipment's map (apps-ai-iot.md, Shipment detail): the road route the hub stored at
// assignment, the pickup and drop, and the driver's last reported position. Nothing here is
// computed: the line is the hub's geometry, the dot is the hub's last ping.
import "leaflet/dist/leaflet.css";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from "react-leaflet";
import { formatDateTime } from "@care-e/ui";
import type { ShipmentDetail } from "../api";
import { TILE_ATTRIBUTION, TILE_URL } from "../display";

type LatLng = [number, number];

/** GeoJSON LineString coordinates are [lng, lat]; Leaflet wants [lat, lng]. */
export function routeLine(geometry: ShipmentDetail["route_geometry"]): LatLng[] {
  const coordinates = (geometry as { type?: string; coordinates?: unknown } | null)?.coordinates;
  if (geometry?.type !== "LineString" || !Array.isArray(coordinates)) return [];
  return coordinates
    .filter((c): c is [number, number] => Array.isArray(c) && c.length >= 2)
    .map(([lng, lat]) => [lat, lng]);
}

const COLORS = { pickup: "#15803d", drop: "#b91c1c", driver: "#1d4ed8", route: "#2563eb" };

export function RouteMap({ shipment }: { shipment: ShipmentDetail }) {
  const line = routeLine(shipment.route_geometry);
  const { pickup, drop, last_location: here } = shipment;
  const points: LatLng[] = [
    ...line,
    ...[pickup, drop, here].filter((p) => p !== null).map((p): LatLng => [p.lat, p.lng]),
  ];
  if (points.length === 0)
    return (
      <p className="p-6 text-center text-sm text-muted-foreground">
        The hub has no locations for this shipment yet.
      </p>
    );

  const single =
    points.length === 1 || points.every((p) => p[0] === points[0]![0] && p[1] === points[0]![1]);
  return (
    <MapContainer
      // Remount when the route arrives or is cleared, so the view fits the new bounds.
      key={line.length > 0 ? "routed" : "unrouted"}
      className="h-72 w-full rounded-md sm:h-96"
      {...(single
        ? { center: points[0], zoom: 14 }
        : { bounds: points, boundsOptions: { padding: [24, 24] } })}
      scrollWheelZoom={false}
    >
      <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
      {line.length > 1 && (
        <Polyline positions={line} pathOptions={{ color: COLORS.route, weight: 5, opacity: 0.8 }} />
      )}
      {pickup && (
        <CircleMarker
          center={[pickup.lat, pickup.lng]}
          radius={8}
          pathOptions={{ color: COLORS.pickup, fillOpacity: 0.9 }}
        >
          <Tooltip>Pickup: {pickup.place}</Tooltip>
        </CircleMarker>
      )}
      {drop && (
        <CircleMarker
          center={[drop.lat, drop.lng]}
          radius={8}
          pathOptions={{ color: COLORS.drop, fillOpacity: 0.9 }}
        >
          <Tooltip>Drop: {drop.place}</Tooltip>
        </CircleMarker>
      )}
      {here && (
        <CircleMarker
          center={[here.lat, here.lng]}
          radius={10}
          pathOptions={{ color: COLORS.driver, fillOpacity: 1 }}
        >
          <Tooltip permanent>Driver, {formatDateTime(here.ts)}</Tooltip>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
