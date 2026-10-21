/**
 * EMI plans (iteration 203 E6). Ingest builds one plan per converted purchase from the statement's
 * principal / interest / GST lines and the bank's booking message; the Recurring page and the card
 * page show it as ONE item instead of an "INTEREST ON EMI ₹291.89/mo" row that carried only the
 * interest.
 */

import { autoFormatCurrency } from "@/lib/formatters";
import { MONTH_SHORT } from "@/lib/constants";
import { parseLocalDate } from "@/lib/dates";

/** Ingest GET /emi-plans → plans[] (also `emi_plan` on a /recurring/detect row). */
export interface EmiPlan {
  plan_id?: string;
  series_key?: string;
  bank?: string | null;
  card_last4?: string | null;
  merchant: string;
  purchase_amount?: number | null;
  purchase_date?: string | null;
  tenure_months?: number | null;
  interest_rate_pct?: number | null;
  processing_fee?: number | null;
  /** The EMI the bank quoted at booking (principal + interest). */
  emi_amount_bank?: number | null;
  first_emi_date?: string | null;
  end_date?: string | null;
  next_due?: string | null;
  installments_billed?: number | null;
  installments_paid?: number | null;
  installments_remaining?: number | null;
  /** The latest instalment as billed: principal + interest + GST on the interest. */
  monthly_payment?: number | null;
  interest_paid?: number | null;
  tax_paid?: number | null;
  interest_and_tax_paid?: number | null;
  principal_remaining?: number | null;
  /** Instalments the bank's schedule has charged that no statement prints yet, estimated from the
   * plan's own rate so the money figures cover every paid instalment (E6 round 2). */
  estimated_installments?: number | null;
  /** How many instalments the interest / principal figures cover (billed + estimated). */
  figures_installments?: number | null;
  status?: string | null;
}

export interface EmiPlanView {
  title: string;
  card: string | null;
  purchase: string | null;
  tenure: string | null;
  monthly: number | null;
  bankEmi: number | null;
  progressText: string;
  progressPct: number | null;
  endLabel: string | null;
  nextDue: string | null;
  interestPaid: number;
  principalRemaining: number | null;
  /** How the money figures relate to "x of n paid", when they are not simply every paid one. */
  figuresNote: string | null;
  statusLabel: string;
}

function instalments(n: number): string {
  return `${n} instalment${n === 1 ? "" : "s"}`;
}

/**
 * "x of n paid" follows the bank's schedule; the interest and principal figures come from the
 * statements. Ingest estimates an instalment the schedule has charged but no statement prints yet
 * (it knows the rate) and says so; without a rate the figures stop at the statements. Either way the
 * card says which (E6 round 2: "3 of 6 paid" beside figures after 2 instalments read as wrong).
 */
function figuresNote(plan: EmiPlan): string | null {
  const estimated = plan.estimated_installments ?? 0;
  if (estimated > 0) {
    const verb = estimated === 1 ? "instalment the bank has" : "instalments the bank has";
    return `Includes ${estimated} ${verb} charged that no statement shows yet (estimated).`;
  }
  const covered = plan.figures_installments;
  const paid = plan.installments_paid;
  if (typeof covered === "number" && typeof paid === "number" && covered < paid) {
    return `Interest and principal cover the ${instalments(covered)} on statements.`;
  }
  return null;
}

function day(iso: string | null | undefined): string | null {
  const d = parseLocalDate(iso);
  return d ? `${d.getDate()} ${MONTH_SHORT[d.getMonth()]} ${d.getFullYear()}` : null;
}

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  completed: "Completed",
  ended: "Ended",
};

export function emiPlanView(plan: EmiPlan): EmiPlanView {
  const paid = plan.installments_paid ?? null;
  const tenure = plan.tenure_months ?? null;
  const progressPct =
    paid !== null && tenure ? Math.min(100, Math.round((paid / tenure) * 100)) : null;
  const progressText =
    paid !== null && tenure ? `${paid} of ${tenure} paid` : paid !== null ? `${paid} paid` : "";
  const bought = day(plan.purchase_date);
  const purchase =
    typeof plan.purchase_amount === "number"
      ? `${autoFormatCurrency(plan.purchase_amount)}${bought ? ` bought ${bought}` : ""}`
      : null;
  const tenureText = tenure
    ? `${tenure} months${
        typeof plan.interest_rate_pct === "number" ? ` at ${plan.interest_rate_pct}% p.a.` : ""
      }`
    : null;
  const end = day(plan.end_date);
  return {
    title: plan.merchant,
    card: plan.card_last4 ? `${plan.bank ? `${plan.bank} ` : ""}··${plan.card_last4}` : null,
    purchase,
    tenure: tenureText,
    monthly: typeof plan.monthly_payment === "number" ? plan.monthly_payment : null,
    bankEmi: typeof plan.emi_amount_bank === "number" ? plan.emi_amount_bank : null,
    progressText,
    progressPct,
    endLabel: end ? `Ends ${end}` : null,
    nextDue: day(plan.next_due),
    interestPaid: plan.interest_and_tax_paid ?? (plan.interest_paid ?? 0) + (plan.tax_paid ?? 0),
    principalRemaining: plan.principal_remaining ?? null,
    figuresNote: figuresNote(plan),
    statusLabel: STATUS_LABELS[plan.status ?? ""] ?? "Active",
  };
}

/** A /recurring/detect row that is an EMI plan (and so renders as the plan card). */
export function isEmiPlanItem(item: { type?: string; emi_plan?: EmiPlan | null }): boolean {
  return item.type === "emi" && !!item.emi_plan;
}
