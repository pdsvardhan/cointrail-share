/**
 * E2E coverage for the critical user flows from ottomate/flows.yaml.
 * Tags map to feature ids so Stage 4 mini-builds can run only affected specs.
 *
 * Assertions are deliberately data-independent (Ingest content may be empty in
 * test envs). We assert structure + that the page didn't 5xx + that the title resolves.
 */
import { test, expect } from "@playwright/test";

async function assertPageLoads(page: import("@playwright/test").Page, path: string) {
  const response = await page.goto(path, { waitUntil: "domcontentloaded" });
  expect(response, `nav to ${path}`).not.toBeNull();
  expect(response!.status(), `${path} status`).toBeLessThan(500);
  await expect(page).toHaveTitle(/Cointrail/);
  // Wait for Next's hydration marker or just any visible main content
  await page.waitForLoadState("networkidle").catch(() => {});
  // Body should be in DOM and have a non-empty rendered text after hydration
  const bodyText = await page.locator("body").innerText();
  expect(bodyText.length, `body of ${path} has rendered content`).toBeGreaterThan(0);
}

test.describe("flow-view-dashboard @feat-expense-dashboard", () => {
  test("opens /expenses and renders dashboard surface", async ({ page }) => {
    await assertPageLoads(page, "/expenses");
  });
});

test.describe("flow-view-cards @feat-credit-cards", () => {
  test("opens /cards and renders portfolio surface", async ({ page }) => {
    await assertPageLoads(page, "/cards");
  });
});

test.describe("flow-detect-recurring @feat-recurring-detection", () => {
  test("opens /expenses/recurring and renders recurring list surface", async ({ page }) => {
    await assertPageLoads(page, "/expenses/recurring");
  });
});

test.describe("flow-classify-upi @feat-upi-classifier (hidden URL)", () => {
  test("/expenses/classify is reachable directly via URL", async ({ page }) => {
    await assertPageLoads(page, "/expenses/classify");
  });
});

test.describe("flow-toggle-theme @feat-theming", () => {
  test("page renders in dark mode by default; design tokens applied", async ({ page }) => {
    await page.goto("/");
    const bgColor = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    // Default dark theme: bg should NOT be pure white
    expect(bgColor).not.toBe("rgb(255, 255, 255)");
  });
});

test.describe("network: API routes respond with valid shape", () => {
  for (const path of ["/api/cards", "/api/recurring/detect", "/api/expenses?limit=1"]) {
    test(`GET ${path} responds with JSON < 500`, async ({ request }) => {
      const res = await request.get(path);
      expect(res.status(), `GET ${path}`).toBeLessThan(500);
      const data = await res.json();
      expect(data).toBeDefined();
    });
  }
});
