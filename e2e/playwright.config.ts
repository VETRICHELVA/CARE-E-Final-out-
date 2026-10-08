import { defineConfig } from "@playwright/test";

const APPS = { "hospital-web": 5173, "supplier-web": 5174, "delivery-web": 5175 };

// Needs Postgres and Redis up, migrated and seeded first: `make up migrate seed`.
// Servers already running (e.g. `make hub`) are reused.
// PLAYWRIGHT_CHROMIUM_PATH points at a preinstalled Chromium when the bundled revision is
// missing, e.g. /opt/pw-browsers/chromium in the cloud container (never `playwright install` there).
const chromium = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export default defineConfig({
  testDir: "tests",
  use: {
    trace: "retain-on-failure",
    ...(chromium ? { launchOptions: { executablePath: chromium } } : {}),
  },
  webServer: [
    {
      command: "uv run uvicorn --factory app.main:create_app --port 8000",
      cwd: "../services/hub-api",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: true,
    },
    ...Object.entries(APPS).map(([app, port]) => ({
      command: `pnpm --filter ${app} dev`,
      url: `http://localhost:${port}`,
      reuseExistingServer: true,
    })),
  ],
});
