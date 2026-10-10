import { type AppConfig, CareApp } from "@care-e/ui";
import { SHIPMENT_ASSIGN, SHIPMENT_UPDATE_STATUS } from "./display";
import { DispatchPage } from "./pages/dispatch";
import { DriverJobsPage } from "./pages/driver-jobs";
import { FleetPage } from "./pages/fleet";
import { RoutePlannerPage } from "./pages/route-planner";
import { ShipmentDetailPage } from "./pages/shipment-detail";

// Routes and screens: apps-ai-iot.md, delivery-web. A nav `capability` mirrors the hub's check:
// the fleet and the route planner (S16) need `shipment.assign`, driver jobs `shipment.update_status`.
// The dispatch board is open to every logistics user; it sends a driver to their jobs.
export const config: AppConfig = {
  name: "CARE-E Delivery",
  allow: ["LOGISTICS"],
  refusal: "This app is for logistics users",
  nav: [
    { to: "/", label: "Dispatch board", element: <DispatchPage /> },
    {
      to: "/plan",
      label: "Route planner",
      capability: SHIPMENT_ASSIGN,
      element: <RoutePlannerPage />,
    },
    { to: "/fleet", label: "Fleet", capability: SHIPMENT_ASSIGN, element: <FleetPage /> },
    {
      to: "/driver",
      label: "Driver jobs",
      capability: SHIPMENT_UPDATE_STATUS,
      element: <DriverJobsPage />,
    },
  ],
  routes: [{ path: "/shipments/:id", element: <ShipmentDetailPage /> }],
  liveUpdates: true,
  // The hub sends cold-chain events to the shipment's carrier (S15); each toast opens it.
  coldChainAlerts: { link: (id) => `/shipments/${id}` },
};

export const App = () => <CareApp {...config} />;
