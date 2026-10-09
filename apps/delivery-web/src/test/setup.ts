// Leaflet draws with real layout and canvas, which jsdom lacks: every test gets the plain
// stand-ins in ./leaflet.tsx, which show what the map was given.
import { configure } from "@testing-library/react";
import { vi } from "vitest";

vi.mock("react-leaflet", () => import("./leaflet"));

// Under a parallel `pnpm test` a screen's first render can exceed Testing Library's 1 s
// default for `findBy`/`waitFor` (as in hospital-web); allow more time per wait.
configure({ asyncUtilTimeout: 4000 });
