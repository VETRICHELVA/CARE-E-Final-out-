import { type AppConfig, CareApp } from "@care-e/ui";
import { SHORTAGE_READERS } from "./display";
import { DashboardPage } from "./pages/dashboard";
import { InventoryPage } from "./pages/inventory";
import { ShortageDetailPage } from "./pages/shortage-detail";
import { ShortagesPage } from "./pages/shortages";

// Routes and screens: apps-ai-iot.md, hospital-web. Screens without an element are placeholders
// until their sections land. A nav `capability` mirrors the hub's check on that screen's list.
export const config: AppConfig = {
  name: "CARE-E Hospital",
  allow: ["HOSPITAL", "PLATFORM"],
  refusal: "This app is for hospital users",
  nav: [
    { to: "/", label: "Dashboard", element: <DashboardPage /> },
    { to: "/inventory", label: "Inventory", element: <InventoryPage /> },
    {
      to: "/shortages",
      label: "Shortages",
      capability: SHORTAGE_READERS,
      element: <ShortagesPage />,
    },
    { to: "/requests", label: "Requests", capability: "source_request.respond" },
    { to: "/deliveries", label: "Deliveries" },
    { to: "/forecasts", label: "Forecasts" },
  ],
  routes: [{ path: "/shortages/:id", element: <ShortageDetailPage /> }],
};

export const App = () => <CareApp {...config} />;
