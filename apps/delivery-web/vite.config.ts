/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The hub is reached through this proxy, so calls are same-origin and the hub needs no CORS here.
  server: { port: 5175, strictPort: true, proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom" },
});
