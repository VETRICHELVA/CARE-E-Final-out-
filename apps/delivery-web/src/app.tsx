import { type AppConfig, CareApp } from "@care-e/ui";

// Routes and screens: apps-ai-iot.md, delivery-web. Pages are placeholders until their sections land.
export const config: AppConfig = {
  name: "CARE-E Delivery",
  allow: ["LOGISTICS"],
  refusal: "This app is for logistics users",
  nav: [
    { to: "/", label: "Dispatch board" },
    { to: "/plan", label: "Route planner" },
    { to: "/fleet", label: "Fleet" },
    { to: "/driver", label: "Driver jobs" },
  ],
};

export const App = () => <CareApp {...config} />;
