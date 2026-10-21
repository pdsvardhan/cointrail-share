/**
 * Lost Pixel visual regression config.
 * Runs against the live container; baseline screenshots stored at .lostpixel/baseline/.
 *
 * Usage:
 *   npx lost-pixel update      # capture baseline (first run + when intentional changes)
 *   npx lost-pixel             # diff against baseline; non-zero exit on diff
 */
import type { CustomProjectConfig } from "lost-pixel";

const config: CustomProjectConfig = {
  pageShots: {
    pages: [
      { path: "/", name: "homepage" },
      { path: "/expenses", name: "expenses-overview" },
      { path: "/expenses/recurring", name: "recurring" },
      { path: "/expenses/classify", name: "classify" },
      { path: "/cards", name: "cards" },
    ],
    breakpoints: [1440, 768],
    baseUrl: process.env.LOST_PIXEL_BASE_URL || "http://localhost:8501",
  },
  lostPixelProjectId: "cointrail",
  imagePathBaseline: ".lostpixel/baseline",
  imagePathCurrent: ".lostpixel/current",
  imagePathDifference: ".lostpixel/difference",
  threshold: 0.005, // 0.5% pixel diff tolerance
  generateOnly: false,
};

export default config;
