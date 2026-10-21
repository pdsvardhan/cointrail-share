/**
 * Small UI state helpers (iteration 203 E2).
 */

/**
 * E2 B5: the mobile card carousel kept its index when a filter left fewer cards, so every card was
 * hidden (it showed card #5 of 2). The index it renders is always inside the list.
 */
export function clampIndex(index: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(Math.max(0, index), total - 1);
}
