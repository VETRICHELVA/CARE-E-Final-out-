import { expect, type Page, test } from "@playwright/test";

// Seeded by `make seed` (services/hub-api/app/seed.py); SEED_PASSWORD defaults to this.
const PASSWORD = "care-e-dev";
const URL = {
  hospital: "http://localhost:5173",
  supplier: "http://localhost:5174",
  delivery: "http://localhost:5175",
};
const REFUSAL = {
  hospital: "This app is for hospital users",
  supplier: "This app is for supplier users",
  delivery: "This app is for logistics users",
};
type App = keyof typeof URL;

// The hub allows 5 logins per minute per IP, so each user signs in once through the UI and
// the other apps get a copy of that session (each app keeps its tokens per origin and tab).
const USERS: { email: string; org: string; app: App; refusedBy: App[] }[] = [
  {
    email: "approver@hospital-a.local",
    org: "Hospital A",
    app: "hospital",
    refusedBy: ["supplier", "delivery"],
  },
  {
    email: "supplier.desk@supplier-x.local",
    org: "Supplier X",
    app: "supplier",
    refusedBy: ["hospital"],
  },
  {
    email: "dispatcher@swiftmed.local",
    org: "SwiftMed Logistics",
    app: "delivery",
    refusedBy: ["hospital"],
  },
];

async function signIn(page: Page, app: App, email: string) {
  await page.goto(URL[app]);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
}

for (const user of USERS) {
  test(`${user.org}: ${user.email} signs into ${user.app}-web; ${user.refusedBy.join(", ")} refuse`, async ({
    page,
  }) => {
    await signIn(page, user.app, user.email);
    await expect(page.getByTestId("org-name")).toHaveText(user.org);
    await expect(page.getByRole("navigation")).toBeVisible();

    const session = await page.evaluate(() => sessionStorage.getItem("care-e-auth"));
    expect(session).toBeTruthy();
    // Copy the session into each other app's origin before its scripts run (once per origin).
    await page
      .context()
      .addInitScript(
        (s) => sessionStorage.getItem("care-e-auth") ?? sessionStorage.setItem("care-e-auth", s),
        session!,
      );
    for (const other of user.refusedBy) {
      await page.goto(URL[other]);
      await expect(page.getByText(REFUSAL[other])).toBeVisible();
      await expect(page.getByRole("navigation")).toHaveCount(0);
    }

    // Signing out revokes the session at the hub and returns to the sign-in page.
    await page.goto(URL[user.app]);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });
}
