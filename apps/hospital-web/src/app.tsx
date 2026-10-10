import { type AppConfig, CareApp } from "@care-e/ui";
import { CopilotPanel } from "./copilot/copilot-panel";
import { SHORTAGE_READERS } from "./display";
import { ADMIN_GATE, AdminPage } from "./pages/admin";
import { DashboardPage } from "./pages/dashboard";
import { DeliveriesPage } from "./pages/deliveries";
import { DeliveryDetailPage } from "./pages/delivery-detail";
import { ForecastsPage } from "./pages/forecasts";
import { InventoryPage } from "./pages/inventory";
import { NotificationBell, NotificationsPage } from "./pages/notifications";
import { ReceivePage } from "./pages/receive";
import { RequestsPage } from "./pages/requests";
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
    {
      to: "/requests",
      label: "Requests",
      capability: "source_request.respond",
      element: <RequestsPage />,
    },
    { to: "/deliveries", label: "Deliveries", element: <DeliveriesPage /> },
    { to: "/forecasts", label: "Forecasts", element: <ForecastsPage /> },
    // Network metrics (S20): PLATFORM users with audit.read only.
    { to: "/admin", label: "Admin", ...ADMIN_GATE, element: <AdminPage /> },
  ],
  routes: [
    { path: "/shortages/:id", element: <ShortageDetailPage /> },
    { path: "/deliveries/:id", element: <DeliveryDetailPage /> },
    { path: "/deliveries/:id/receive", element: <ReceivePage /> },
    { path: "/notifications", element: <NotificationsPage /> },
  ],
  liveUpdates: true,
  headerExtra: <NotificationBell />,
  // The copilot (S13) on every screen; it reads the hub as the signed-in user, never writes.
  aside: <CopilotPanel />,
  // Cold-chain events on shipments to this hospital (S15); each toast opens the delivery.
  coldChainAlerts: { link: (id) => `/deliveries/${id}`, inboundOnly: true },
};

export const App = () => <CareApp {...config} />;
