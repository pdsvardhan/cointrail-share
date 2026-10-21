import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for Cointrail E2E + a11y.
 * - Targets the live container at http://localhost:8501 by default.
 * - Override with PLAYWRIGHT_BASE_URL when running against a fresh deploy.
 * - No webServer block: the container is managed by docker-compose, not by Playwright.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["list"], ["junit", { outputFile: "playwright-junit.xml" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8501",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "desktop-chrome",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
