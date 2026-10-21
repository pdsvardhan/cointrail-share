/**
 * a11y baseline — axe-core scan on every page, WCAG 2.1 AA.
 * Phase 5 entry: blocks on `critical` only; `serious` violations are reported
 * but don't fail the build. We tighten to `serious` once existing violations
 * are addressed in a follow-up iteration.
 * @phase-5-a11y
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const pages = ["/", "/expenses", "/expenses/recurring", "/expenses/classify", "/cards"];

for (const path of pages) {
  test(`a11y: no critical axe violations on ${path}`, async ({ page }) => {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const critical = results.violations.filter((v) => v.impact === "critical");
    const serious = results.violations.filter((v) => v.impact === "serious");

    if (serious.length > 0) {
      console.log(
        `\n[a11y][${path}] ${serious.length} 'serious' violation(s) (warn-only):\n`,
        JSON.stringify(
          serious.map((v) => ({ id: v.id, help: v.help, nodes: v.nodes.length })),
          null,
          2
        )
      );
    }
    if (critical.length > 0) {
      console.log(
        `\n[a11y][${path}] ${critical.length} CRITICAL violation(s):\n`,
        JSON.stringify(
          critical.map((v) => ({ id: v.id, help: v.help, nodes: v.nodes.length })),
          null,
          2
        )
      );
    }
    expect(critical, `critical a11y violations on ${path}`).toEqual([]);
  });
}
