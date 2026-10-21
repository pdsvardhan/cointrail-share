/**
 * Calendar-date helpers for the browser (iter-18 M9).
 *
 * Ingest sends due dates, statement dates and transaction dates as plain `YYYY-MM-DD`
 * calendar dates (decided in IST on the server). `new Date("YYYY-MM-DD")` parses that
 * as UTC midnight, which is 05:30 in IST, so a card flipped to "Overdue" at 05:30 IST
 * on its due date while the card page (which parsed `T00:00:00` local) said "0 days
 * overdue". The reverse trip has the same problem: `date.toISOString()` on a local
 * midnight gives the previous day in IST. Everything that turns these strings into a
 * day count, a comparison or a label goes through the helpers below instead.
 */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function parts(iso: string | null | undefined): [number, number, number] | null {
  if (!iso) return null;
  const m = ISO_DATE.exec(iso.trim().slice(0, 10));
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  // Reject impossible dates (e.g. 2026-02-30) instead of letting Date roll them over.
  const probe = new Date(Date.UTC(y, mo - 1, d));
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== mo - 1 || probe.getUTCDate() !== d) {
    return null;
  }
  return [y, mo, d];
}

/** `YYYY-MM-DD` (a leading date in a longer timestamp is accepted) -> local midnight, or null. */
export function parseLocalDate(iso: string | null | undefined): Date | null {
  const p = parts(iso);
  return p ? new Date(p[0], p[1] - 1, p[2]) : null;
}

/** Local calendar date of `d` as `YYYY-MM-DD` (never via toISOString, which is UTC). */
export function toLocalISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Whole calendar days from today (local) to `iso`: 0 on the day itself, negative once it
 * has passed. Counted on calendar components, so DST shifts and the time of day do not
 * move the answer. Null when `iso` is missing or not a valid date.
 */
export function daysUntil(iso: string | null | undefined, now: Date = new Date()): number | null {
  const p = parts(iso);
  if (!p) return null;
  const target = Date.UTC(p[0], p[1] - 1, p[2]);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 864e5);
}
