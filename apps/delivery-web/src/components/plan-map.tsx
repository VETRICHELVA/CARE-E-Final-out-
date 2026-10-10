// The route planner's map: the hub's stops in driving order, joined in that order. The line
// shows the order, not the roads (the hub stores each shipment's road route when it assigns).
import "leaflet/dist/leaflet.css";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from "react-leaflet";
import type { RouteStop } from "../api";
import { TILE_ATTRIBUTION, TILE_URL } from "../display";

type LatLng = [number, number];

const COLORS = { PICKUP: "#15803d", DROP: "#b91c1c", route: "#2563eb" };

export function PlanMap({ stops }: { stops: RouteStop[] }) {
  if (stops.length === 0) return null;
  const line: LatLng[] = stops.map((s) => [s.lat, s.lng]);
  const single = line.every((p) => p[0] === line[0]![0] && p[1] === line[0]![1]);
  return (
    <MapContainer
      // A new plan remounts the map so the view fits its stops.
      key={stops.map((s) => `${s.shipment_id}${s.type}`).join()}
      className="h-72 w-full rounded-md sm:h-96"
      {...(single
        ? { center: line[0], zoom: 14 }
        : { bounds: line, boundsOptions: { padding: [24, 24] } })}
      scrollWheelZoom={false}
    >
      <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
      {line.length > 1 && (
        <Polyline
          positions={line}
          pathOptions={{ color: COLORS.route, weight: 4, opacity: 0.8, dashArray: "6 6" }}
        />
      )}
      {stops.map((stop) => (
        <CircleMarker
          key={`${stop.shipment_id}-${stop.type}`}
          center={[stop.lat, stop.lng]}
          radius={9}
          pathOptions={{ color: COLORS[stop.type], fillOpacity: 0.9 }}
        >
          <Tooltip>
            {stop.seq}. {stop.type === "PICKUP" ? "Pickup" : "Drop"}: {stop.place}
          </Tooltip>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
