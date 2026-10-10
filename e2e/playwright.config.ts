import { defineConfig } from "@playwright/test";

const APPS = { "hospital-web": 5173, "supplier-web": 5174, "delivery-web": 5175 };

// Needs Postgres and Redis up, migrated and seeded first: `make up migrate seed`.
// Servers already running (e.g. `make hub`) are reused.
// PLAYWRIGHT_CHROMIUM_PATH points at a preinstalled Chromium when the bundled revision is
// missing, e.g. /opt/pw-browsers/chromium in the cloud container (never `playwright install` there).
const chromium = process.env.PLAYWRIGHT_CHROMIUM_PATH;

// A local dev hub: an unset APP_ENV counts as production, which refuses the committed dev
// secrets. Set here too so the seed scripts the tests spawn (e2e/seed/*.py) inherit it.
process.env.APP_ENV = "dev";

export default defineConfig({
  testDir: "tests",
  // One spec at a time: the demo scenario specs share the demo seed's orgs and users (Hospital
  // B answers Scenarios 1 and 3, SwiftMed carries every shipment, Scenario 3 reads the
  // network-wide metrics before and after its receipt), and the self-seeding specs re-run the
  // seed, which re-times the scenario batches.
  workers: 1,
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
      env: { APP_ENV: "dev" },
    },
    ...Object.entries(APPS).map(([app, port]) => ({
      command: `pnpm --filter ${app} dev`,
      url: `http://localhost:${port}`,
      reuseExistingServer: true,
    })),
  ],
});
