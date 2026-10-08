// Stand-ins for react-leaflet in jsdom. Each renders the props a test asserts on as data
// attributes, and its children (tooltips) as text.
import type { ReactNode } from "react";

type Props = { children?: ReactNode } & Record<string, unknown>;
const json = (value: unknown) => (value === undefined ? undefined : JSON.stringify(value));

export const MapContainer = ({ children, center, bounds, zoom }: Props) => (
  <div
    data-testid="map"
    data-center={json(center)}
    data-bounds={json(bounds)}
    data-zoom={json(zoom)}
  >
    {children}
  </div>
);
export const TileLayer = ({ url }: Props) => (
  <div data-testid="map-tiles" data-url={url as string} />
);
export const Polyline = ({ positions }: Props) => (
  <div data-testid="map-route" data-positions={json(positions)} />
);
export const CircleMarker = ({ children, center }: Props) => (
  <div data-testid="map-marker" data-center={json(center)}>
    {children}
  </div>
);
export const Tooltip = ({ children }: Props) => <span>{children}</span>;
