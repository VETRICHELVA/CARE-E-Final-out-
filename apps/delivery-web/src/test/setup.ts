// Leaflet draws with real layout and canvas, which jsdom lacks: every test gets the plain
// stand-ins in ./leaflet.tsx, which show what the map was given.
import { vi } from "vitest";

vi.mock("react-leaflet", () => import("./leaflet"));
