import { type AppConfig, CareApp } from "@care-e/ui";

// Routes and screens: apps-ai-iot.md, hospital-web. Pages are placeholders until their sections land.
export const config: AppConfig = {
  name: "CARE-E Hospital",
  allow: ["HOSPITAL", "PLATFORM"],
  refusal: "This app is for hospital users",
  nav: [
    { to: "/", label: "Dashboard" },
    { to: "/inventory", label: "Inventory" },
    { to: "/shortages", label: "Shortages" },
    { to: "/requests", label: "Requests" },
    { to: "/deliveries", label: "Deliveries" },
    { to: "/forecasts", label: "Forecasts" },
  ],
};

export const App = () => <CareApp {...config} />;
