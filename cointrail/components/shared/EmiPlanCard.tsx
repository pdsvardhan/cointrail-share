"use client";

import type { ReactNode } from "react";
import { Calendar, CreditCard } from "lucide-react";
import { autoFormatCurrency } from "@/lib/formatters";
import { emiPlanView, type EmiPlan } from "@/lib/emi";

/**
 * One EMI plan as ONE item (iteration 203 E6): what was bought and when, the tenure and rate, the
 * monthly payment (principal + interest + GST, with the bank's quoted EMI beside it), x of n paid,
 * the end date, the interest + GST paid so far and the next instalment. Used by the Recurring page
 * and the card page.
 */
export function EmiPlanCard({
  plan,
  index = 0,
  footer,
}: {
  plan: EmiPlan;
  index?: number;
  /** e.g. the "Mark as cancelled" control on the Recurring page. */
  footer?: ReactNode;
}) {
  const v = emiPlanView(plan);
  return (
    <div
      className="animate-fade-in rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-colors hover:border-red-500/20"
      style={{ animationDelay: `${index * 50}ms` }}
      data-role="emi-plan"
      data-plan-id={plan.plan_id}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <h3 className="truncate text-sm font-medium text-[var(--foreground)]">{v.title}</h3>
            <span className="flex-shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold text-amber-700 dark:text-amber-400">
              EMI
            </span>
            {v.statusLabel !== "Active" && (
              <span className="flex-shrink-0 rounded bg-[var(--stat-bg)] px-1.5 py-0.5 text-[9px] font-semibold text-[var(--text-muted)]">
                {v.statusLabel}
              </span>
            )}
          </div>
          {v.purchase && (
            <p className="mt-0.5 text-[11px] text-[var(--foreground-secondary)]">{v.purchase}</p>
          )}
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] text-[var(--text-muted)]">
            {v.tenure && <span>{v.tenure}</span>}
            {v.card && (
              <span className="flex items-center gap-0.5">
                <CreditCard className="h-2.5 w-2.5" aria-hidden />
                {v.card}
              </span>
            )}
          </p>
        </div>
        <div className="flex-shrink-0 text-right">
          {v.monthly !== null ? (
            <>
              <span className="font-mono text-sm font-semibold text-red-700 dark:text-red-400">
                {autoFormatCurrency(v.monthly)}
                <span className="ml-1 text-[10px] font-normal text-[var(--text-muted)]">/mo</span>
              </span>
              <p
                className="text-[10px] text-[var(--text-muted)]"
                title="The latest instalment as billed: principal + interest + GST on the interest"
              >
                incl. interest + GST
              </p>
            </>
          ) : (
            <span className="text-[11px] text-[var(--text-muted)]">no instalment billed yet</span>
          )}
          {v.bankEmi !== null && (
            <p className="text-[10px] text-[var(--text-muted)]">
              bank EMI {autoFormatCurrency(v.bankEmi)}
            </p>
          )}
        </div>
      </div>

      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
          <span className="font-mono font-semibold text-[var(--foreground)]">{v.progressText}</span>
          <span>Interest + GST so far: {autoFormatCurrency(v.interestPaid)}</span>
        </div>
        {v.progressPct !== null && (
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--stat-bg)]"
            role="progressbar"
            aria-valuenow={v.progressPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={v.progressText}
          >
            <div
              className="h-full rounded-full bg-gradient-to-r from-red-500 to-red-400 transition-all"
              style={{ width: `${v.progressPct}%` }}
            />
          </div>
        )}
        <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 text-[10px] text-[var(--text-muted)]">
          <span>{v.endLabel ?? ""}</span>
          {v.nextDue && (
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" aria-hidden />
              Next: {v.nextDue}
            </span>
          )}
        </div>
        {v.principalRemaining !== null && v.statusLabel === "Active" && (
          <p className="mt-1 text-[10px] text-[var(--text-muted)]">
            Principal left: {autoFormatCurrency(v.principalRemaining)}
          </p>
        )}
        {v.figuresNote && (
          <p
            className="mt-1 text-[10px] text-[var(--text-muted)] italic"
            data-role="emi-figures-note"
          >
            {v.figuresNote}
          </p>
        )}
      </div>
      {footer}
    </div>
  );
}
