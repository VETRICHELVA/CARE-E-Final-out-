/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The hub is reached through this proxy, so calls are same-origin and the hub needs no CORS here.
  // `/ai/*` is the AI service (`make ai`, port 8100) for the copilot panel (S13).
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ai": { target: "http://127.0.0.1:8100", rewrite: (path) => path.replace(/^\/ai/, "") },
    },
  },
  test: { environment: "jsdom", setupFiles: ["src/test/setup.ts"] },
});
