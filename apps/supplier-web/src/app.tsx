import { type AppConfig, CareApp } from "@care-e/ui";

// Routes and screens: apps-ai-iot.md, supplier-web. Pages are placeholders until their sections land.
export const config: AppConfig = {
  name: "CARE-E Supplier",
  allow: ["SUPPLIER"],
  refusal: "This app is for supplier users",
  nav: [
    { to: "/", label: "Dashboard" },
    { to: "/offers", label: "Catalog and offers" },
    { to: "/orders", label: "Purchase orders" },
    { to: "/demand", label: "Network demand" },
  ],
};

export const App = () => <CareApp {...config} />;
