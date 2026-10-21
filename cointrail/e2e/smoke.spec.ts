/**
 * Smoke + render-without-crash tests for every top-level page.
 * One spec covers all pages so they share Playwright's browser/context cost.
 * Updated iter-15: app shell redesigned — opens on Credit Cards, top-tab
 * switcher (no sidebar/Home), forced dark mode (no theme toggle).
 * @feat-theming @feat-expense-dashboard @feat-credit-cards @feat-recurring-detection @feat-upi-classifier
 */
import { test, expect } from "@playwright/test";

const pages = [
  { path: "/", title: /Cointrail/, mustContain: [] }, // redirects to /cards
  { path: "/cards", title: /Cointrail/, mustContain: [] },
  { path: "/expenses", title: /Cointrail/, mustContain: [] },
  { path: "/expenses/recurring", title: /Cointrail/, mustContain: [] },
];

for (const p of pages) {
  test(`renders ${p.path} without throwing`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (e) => consoleErrors.push(e.message));
    page.on("console", (msg) => {
      // Only fail on uncaught errors, not normal console.error from fetch fallbacks
      if (msg.type() === "error" && /TypeError|ReferenceError|SyntaxError/.test(msg.text())) {
        consoleErrors.push(msg.text());
      }
    });

    const response = await page.goto(p.path, { waitUntil: "domcontentloaded" });
    expect(response, `nav to ${p.path}`).not.toBeNull();
    expect(response!.status(), `${p.path} status`).toBeLessThan(500);
    await expect(page).toHaveTitle(p.title);

    for (const text of p.mustContain) {
      await expect(page.getByText(text).first()).toBeVisible();
    }

    expect(consoleErrors, `JS errors on ${p.path}`).toEqual([]);
  });
}

test("/ redirects to the Credit Cards page", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL("**/cards");
  expect(page.url()).toContain("/cards");
});

test("header shows the three-tab switcher and no sidebar/Home", async ({ page }) => {
  await page.goto("/cards");
  const nav = page.locator("header nav");
  await expect(nav.getByRole("link", { name: /Cards/ })).toBeVisible();
  await expect(nav.getByRole("link", { name: /Expenses/ })).toBeVisible();
  await expect(nav.getByRole("link", { name: /Recurring/ })).toBeVisible();
  // No Home nav entry, no left sidebar
  await expect(page.getByRole("link", { name: /^Home$/ })).toHaveCount(0);
  await expect(page.locator("aside")).toHaveCount(0);
});

test("nav: header Expenses tab navigates to /expenses", async ({ page }) => {
  await page.goto("/cards");
  await page
    .locator("header nav")
    .getByRole("link", { name: /Expenses/ })
    .click();
  await page.waitForURL("**/expenses");
  expect(page.url()).toContain("/expenses");
});

test("dark mode is forced and there is no theme toggle", async ({ page }) => {
  await page.goto("/cards");
  await expect
    .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
    .toBe(true);
  const themeBtn = page.locator('button[aria-label*="theme" i], button[title*="theme" i]');
  await expect(themeBtn).toHaveCount(0);
});
