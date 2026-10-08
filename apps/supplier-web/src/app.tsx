import { type AppConfig, CareApp } from "@care-e/ui";
import { PO_RESPOND } from "./display";
import { DashboardPage } from "./pages/dashboard";
import { DemandPage } from "./pages/demand";
import { OffersPage } from "./pages/offers";
import { OrderDetailPage } from "./pages/order-detail";
import { OrdersPage } from "./pages/orders";

// Routes and screens: apps-ai-iot.md, supplier-web. A nav `capability` mirrors the hub's check
// on that screen's list (purchase orders and network demand need `po.respond`).
export const config: AppConfig = {
  name: "CARE-E Supplier",
  allow: ["SUPPLIER"],
  refusal: "This app is for supplier users",
  nav: [
    { to: "/", label: "Dashboard", element: <DashboardPage /> },
    { to: "/offers", label: "Catalog and offers", element: <OffersPage /> },
    {
      to: "/orders",
      label: "Purchase orders",
      capability: PO_RESPOND,
      element: <OrdersPage />,
    },
    { to: "/demand", label: "Network demand", capability: PO_RESPOND, element: <DemandPage /> },
  ],
  routes: [{ path: "/orders/:id", element: <OrderDetailPage /> }],
  liveUpdates: true,
};

export const App = () => <CareApp {...config} />;
