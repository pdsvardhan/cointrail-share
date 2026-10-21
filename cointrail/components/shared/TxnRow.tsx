"use client";

import type { ReactNode } from "react";
import { motion } from "motion/react";
import { signedAmountParts } from "@/lib/txn-display";

/**
 * One transaction in a list (iteration 203 E2). Every transaction list on the Expenses and card
 * detail pages renders through this row, so a per-row control is added in one place: pass it as
 * `actions`. The amount is SIGNED — a refund / cashback is negative and shown "+", green (M3 / M4).
 * Rows only take a hover style when they do something (`hover`), so nothing looks clickable that
 * is not (C6).
 */
export interface TxnRowProps {
  title: string;
  /** Second line: date, category, card, ... */
  meta?: ReactNode;
  /** Positive = money out, negative = money back. */
  signedAmount: number;
  /** Icon or logo on the left. */
  leading?: ReactNode;
  /** Per-row controls, rendered before the amount. */
  actions?: ReactNode;
  /** Extra lines under the title (e.g. order items). */
  children?: ReactNode;
  index?: number;
  hover?: boolean;
  /** Write debits as "-₹..." (the card page's style). */
  debitSign?: boolean;
  size?: "sm" | "md";
  txnId?: number;
}

export function TxnRow({
  title,
  meta,
  signedAmount,
  leading,
  actions,
  children,
  index = 0,
  hover = false,
  debitSign = false,
  size = "sm",
  txnId,
}: TxnRowProps) {
  const { text, isCredit } = signedAmountParts(signedAmount, debitSign);
  return (
    <motion.div
      data-txn-id={txnId}
      data-role="txn-row"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.02, 0.3) }}
      className={`flex items-center gap-2.5 rounded-lg px-2 ${size === "md" ? "py-3" : "py-1.5"} ${
        hover ? "transition-colors hover:bg-[var(--card-hover)]" : ""
      }`}
    >
      {leading}
      <div className="min-w-0 flex-1">
        <div
          className={`truncate font-medium text-[var(--foreground)] ${size === "md" ? "text-sm" : "text-xs"}`}
        >
          {title}
        </div>
        {meta && <div className="mt-0.5 text-[10px] text-[var(--text-muted)]">{meta}</div>}
        {children}
      </div>
      {actions}
      <span
        className={`flex-shrink-0 font-mono font-semibold ${size === "md" ? "text-sm" : "text-xs"} ${
          isCredit ? "text-emerald-600 dark:text-emerald-400" : "text-[var(--foreground)]"
        }`}
        title={isCredit ? "Money back (refund / cashback) — reduces spending" : undefined}
      >
        {text}
      </span>
    </motion.div>
  );
}
