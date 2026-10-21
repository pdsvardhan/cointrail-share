/**
 * Card payment status as reported by Ingest /cards/summary (iter-18, brief contract 3).
 *
 * Ingest now decides the status on the server, in IST: `payment_status`, `remaining_due`
 * (amount owed after payments since the statement closed), `current_cycle` (statement
 * closed within the last 45 IST days) and `latest_statement.{due_date, due_date_source,
 * amount_due}`. The browser used to decide "overdue" itself, from a UTC-parsed due date
 * (M9) and a gross statement total (H2). These helpers prefer the server's fields and fall
 * back to the old fields only when talking to an older Ingest, so the page never crashes.
 */

import { daysUntil } from "./dates";

export const PAYMENT_STATUSES = [
  "overdue",
  "partial",
  "unpaid",
  "not_confirmed",
  "paid",
  "no_dues",
] as const;

export type PaymentStatus = (typeof PAYMENT_STATUSES)[number];

export interface StatusStatement {
  total?: number | null;
  billing_end?: string | null;
  due_date?: string | null;
  due_date_source?: string | null;
  amount_due?: number | null;
}

export interface StatusCard {
  latest_statement: StatusStatement | null;
  is_paid: boolean;
  payment?: { total: number } | null;
  /** Bank-stated amount due (pre-contract Ingest field, from the statement SMS). */
  actual_due?: number | null;
  payment_status?: string | null;
  remaining_due?: number | null;
  current_cycle?: boolean | null;
}

/** Statuses that mean money is still owed on a known bill (counted in Total Due). */
const OWING: ReadonlySet<PaymentStatus> = new Set(["unpaid", "partial", "overdue"]);

/** Same window Ingest uses for `current_cycle`. */
const CURRENT_CYCLE_DAYS = 45;

function isPaymentStatus(v: unknown): v is PaymentStatus {
  return typeof v === "string" && (PAYMENT_STATUSES as readonly string[]).includes(v);
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function isOwing(status: PaymentStatus): boolean {
  return OWING.has(status);
}

/** Paid or nothing to pay: the card needs no action. */
export function isSettled(status: PaymentStatus): boolean {
  return status === "paid" || status === "no_dues";
}

/**
 * The statement's bill: Ingest's `amount_due` (bank-stated total, else net computed), else
 * the older `actual_due` (bank-stated), else the statement total.
 */
export function statementAmountDue(card: StatusCard): number {
  const s = card.latest_statement;
  if (!s) return 0;
  return num(s.amount_due) ?? num(card.actual_due) ?? num(s.total) ?? 0;
}

/** Payments received since the statement closed (0 when none were captured). */
function paidSinceClose(card: StatusCard): number {
  return num(card.payment?.total) ?? 0;
}

export function resolvePaymentStatus(card: StatusCard, now: Date = new Date()): PaymentStatus {
  if (isPaymentStatus(card.payment_status)) return card.payment_status;
  // Older Ingest (no payment_status): reproduce its is_paid verdict, and decide overdue
  // on the local calendar date (never a UTC parse, M9).
  const s = card.latest_statement;
  if (!s) return "no_dues";
  if (card.is_paid) return "paid";
  if (statementAmountDue(card) <= 0) return "no_dues";
  const days = daysUntil(s.due_date, now);
  if (days !== null && days < 0) return "overdue";
  return paidSinceClose(card) > 0 ? "partial" : "unpaid";
}

/** Amount still owed on the latest statement; 0 once it is paid or there are no dues. */
export function remainingDue(card: StatusCard, now: Date = new Date()): number {
  const server = num(card.remaining_due);
  if (server !== null) return Math.max(server, 0);
  if (isSettled(resolvePaymentStatus(card, now))) return 0;
  return Math.max(statementAmountDue(card) - paidSinceClose(card), 0);
}

/** Whether the latest statement belongs to the current billing cycle. */
export function isCurrentCycle(card: StatusCard, now: Date = new Date()): boolean {
  if (typeof card.current_cycle === "boolean") return card.current_cycle;
  // Older Ingest: same rule as the server, closed within the last 45 days.
  const days = daysUntil(card.latest_statement?.billing_end, now);
  if (days === null) return false;
  return days <= 0 && -days <= CURRENT_CYCLE_DAYS;
}

/**
 * Words a local day count for the due-date line. `overdue` is Ingest's verdict: a past
 * date on a bill that is not overdue (e.g. payment not confirmed) reads "N days ago", and
 * an overdue bill never reads "0 days overdue" (the M9 symptom on the card page).
 */
export function dueDayLabel(days: number, overdue: boolean): string {
  const plural = (n: number) => `${n} day${n !== 1 ? "s" : ""}`;
  if (days < 0) return `${plural(-days)} ${overdue ? "overdue" : "ago"}`;
  if (overdue) return "Overdue";
  if (days === 0) return "Due today";
  return `${plural(days)} remaining`;
}

/** True when the due date shown is Ingest's estimate rather than the bank's date. */
export function isDueDateEstimated(card: StatusCard): boolean {
  return card.latest_statement?.due_date_source === "estimated";
}

export interface CardsSummary {
  /** Sum of remaining dues over unpaid / partial / overdue cards. */
  totalDue: number;
  owingCount: number;
  notConfirmedCount: number;
  /** Payments on statements of the current cycle only (dormant cards' old payments excluded). */
  paidThisCycle: number;
  currentCycleCount: number;
  currentCycleSettledCount: number;
}

export function summarizeCards(cards: StatusCard[], now: Date = new Date()): CardsSummary {
  const out: CardsSummary = {
    totalDue: 0,
    owingCount: 0,
    notConfirmedCount: 0,
    paidThisCycle: 0,
    currentCycleCount: 0,
    currentCycleSettledCount: 0,
  };
  for (const c of cards) {
    const status = resolvePaymentStatus(c, now);
    if (isOwing(status)) {
      out.totalDue += remainingDue(c, now);
      out.owingCount += 1;
    } else if (status === "not_confirmed") {
      out.notConfirmedCount += 1;
    }
    if (c.latest_statement && isCurrentCycle(c, now)) {
      out.currentCycleCount += 1;
      out.paidThisCycle += paidSinceClose(c);
      if (isSettled(status)) out.currentCycleSettledCount += 1;
    }
  }
  out.totalDue = Math.round(out.totalDue * 100) / 100;
  out.paidThisCycle = Math.round(out.paidThisCycle * 100) / 100;
  return out;
}
