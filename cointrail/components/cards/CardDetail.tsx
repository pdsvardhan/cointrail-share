"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  CreditCard,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Hourglass,
  Calendar,
  TrendingUp,
  Receipt,
  Activity,
  Banknote,
  ArrowUpRight,
  ArrowDownLeft,
  Clock,
  Wallet,
  HelpCircle,
} from "lucide-react";
import { autoFormatCurrency } from "@/lib/formatters";
import { CATEGORY_COLORS, MONTH_SHORT, getCardColors } from "@/lib/constants";
import { getMerchantLogoUrl } from "@/lib/logos";
import { daysUntil, parseLocalDate, toLocalISODate } from "@/lib/dates";
import {
  cardPeriodLabel,
  cardPeriodOptions,
  cardTransactionsQuery,
  defaultCardPeriod,
  type CardPeriodKey,
} from "@/lib/card-periods";
import { errorMessage, getJson } from "@/lib/http";
import {
  netRefundNote,
  sameBillNote,
  splitCategoryBreakdown,
  unbilledSummary,
} from "@/lib/card-detail";
import { ErrorState } from "@/components/shared/ErrorState";
import { TxnRow } from "@/components/shared/TxnRow";
import { ChangeCategoryButton } from "@/components/shared/ChangeCategory";
import { EmiPlanCard } from "@/components/shared/EmiPlanCard";
import type { EmiPlan } from "@/lib/emi";
import {
  dueDayLabel,
  isDueDateEstimated,
  isSettled,
  remainingDue,
  resolvePaymentStatus,
  statementAmountDue,
} from "@/lib/card-status";

// ─── Types ──────────────────────────────────────────────────────────────────

interface PaymentInfo {
  total: number;
  count: number;
  latest_date: string | null;
  payments: { amount: number; date: string }[];
}

interface CardInfo {
  id: number;
  bank: string;
  card_name: string;
  last4: string;
  network: string | null;
  billing_group?: string | null;
  statement_day: number | null;
  due_day: number | null;
  latest_statement: {
    month: number;
    year: number;
    total: number;
    txn_count: number;
    billing_start: string;
    billing_end: string;
    due_date: string | null;
    // iter-18 contract 3 (absent on an older Ingest):
    due_date_source?: "bank" | "estimated" | null;
    amount_due?: number | null;
    stated_total?: number | null;
    computed_total?: number | null;
    min_due?: number | null;
  } | null;
  is_paid: boolean;
  payment: PaymentInfo | null;
  unbilled: { total: number; count: number; credits?: { total: number; count: number } | null };
  total_spend: number;
  total_txns: number;
  actual_due?: number | null;
  // iter-18 contract 3: decided by Ingest in IST.
  payment_status?: string | null;
  remaining_due?: number | null;
  current_cycle?: boolean | null;
}

interface Transaction {
  id: number;
  amount: number;
  merchant: string | null;
  raw_description: string | null;
  category: string | null;
  date: string;
  txn_type: string;
  kind?: string | null;
  card_last4?: string | null;
}

// Ingest /cards/{id}/transactions (iteration 203 E2): the aggregates count spending only and cover
// every matching row; the list alone is capped (count vs total_count).
interface TxnData {
  transactions: Transaction[];
  /** Net spend (refunds / cashback netted; bill payments and EMI principal never added). */
  total: number;
  count: number;
  total_count?: number;
  refunds?: { total: number; count: number };
  categories: Record<string, number>;
  category_breakdown?: {
    category: string;
    total: number;
    count: number;
    merchants: { name: string; total: number; count: number }[];
  }[];
  top_merchants: {
    name: string;
    total: number;
    count: number;
    category: string;
  }[];
  card_last4s?: string[];
  unbilled_since?: string;
}

// ─── Constants ──────────────────────────────────────────────────────────────

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

// ─── Helpers ────────────────────────────────────────────────────────────────

function getCatColor(cat: string): string {
  return CATEGORY_COLORS[cat] || "#64748b";
}

function formatDateShort(dateStr: string | null): string {
  const d = parseLocalDate(dateStr);
  return d ? `${d.getDate()} ${MONTH_SHORT[d.getMonth()]}` : (dateStr ?? "—");
}

function getMonthYearLabel(dateStr: string): string {
  const d = parseLocalDate(dateStr);
  return d ? `${MONTH_NAMES[d.getMonth()]} ${d.getFullYear()}` : dateStr;
}

function getNextStatementDate(card: CardInfo): string | null {
  if (!card.statement_day) return null;
  const now = new Date();
  let nextMonth = now.getMonth();
  let nextYear = now.getFullYear();
  if (now.getDate() >= card.statement_day) {
    nextMonth += 1;
    if (nextMonth > 11) {
      nextMonth = 0;
      nextYear += 1;
    }
  }
  // Local date: toISOString() here showed "Next stmt" one day early in IST (M9).
  return toLocalISODate(new Date(nextYear, nextMonth, card.statement_day));
}

// ─── Sub-Components ─────────────────────────────────────────────────────────

function MerchantLogo({ merchant }: { merchant: string }) {
  return (
    <div className="relative flex-shrink-0">
      <img
        src={getMerchantLogoUrl(merchant) ?? undefined}
        alt=""
        className="h-9 w-9 rounded-xl bg-[var(--input)] object-cover"
        onError={(e) => {
          const el = e.target as HTMLImageElement;
          el.style.display = "none";
          const parent = el.parentElement;
          if (parent && !parent.querySelector(".fallback-icon")) {
            const fallback = document.createElement("div");
            fallback.className =
              "fallback-icon w-9 h-9 rounded-xl bg-[var(--input)] flex items-center justify-center text-[var(--text-muted)] text-xs font-semibold flex-shrink-0";
            fallback.textContent = merchant.charAt(0).toUpperCase();
            parent.insertBefore(fallback, el);
          }
        }}
      />
    </div>
  );
}

const KIND_NOTES: Record<string, string> = {
  bill_payment: "bill payment · not spending",
  emi_principal: "EMI principal · counted at purchase",
  emi_conversion: "EMI conversion · not spending",
};

function TransactionRow({
  txn,
  index,
  showCard,
  onChanged,
}: {
  txn: Transaction;
  index: number;
  showCard: boolean;
  /** After the row is re-filed (iteration 203 E3): refresh the list and the breakdowns. */
  onChanged: () => void;
}) {
  const cat = txn.category || "Uncategorized";
  const color = getCatColor(cat);
  const merchant = txn.merchant || txn.raw_description || "Unknown";
  const isCredit = txn.txn_type === "credit" || txn.amount < 0;
  const note = txn.kind ? KIND_NOTES[txn.kind] : undefined;
  const signed = isCredit ? -Math.abs(txn.amount) : Math.abs(txn.amount);

  return (
    <TxnRow
      txnId={txn.id}
      index={index}
      size="md"
      debitSign
      leading={<MerchantLogo merchant={merchant} />}
      title={merchant}
      meta={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-[11px]">{formatDateShort(txn.date)}</span>
          <span
            className="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
            style={{ color, backgroundColor: `${color}15` }}
          >
            {cat}
          </span>
          {showCard && txn.card_last4 && <span>Card {txn.card_last4}</span>}
          {note && <span>{note}</span>}
        </span>
      }
      signedAmount={signed}
      actions={
        <ChangeCategoryButton
          txnId={txn.id}
          title={merchant}
          category={txn.category}
          amount={signed}
          onChanged={onChanged}
        />
      }
    />
  );
}

// ─── Main Component ─────────────────────────────────────────────────────────

export default function CardDetail({ cardId }: { cardId: string }) {
  const [card, setCard] = useState<CardInfo | null>(null);
  // Every card billed together with this one (an add-on and its primary, iteration 203 E2 M9).
  const [group, setGroup] = useState<CardInfo[]>([]);
  const [cardError, setCardError] = useState<string | null>(null);
  const [cardAttempt, setCardAttempt] = useState(0);
  const [txnData, setTxnData] = useState<TxnData | null>(null);
  const [txnError, setTxnError] = useState<string | null>(null);
  const [txnAttempt, setTxnAttempt] = useState(0);
  // null = the default: the current statement when one was captured, else the last 3 months.
  const [periodChoice, setPeriod] = useState<CardPeriodKey | null>(null);
  const [periodOpen, setPeriodOpen] = useState(false);
  const [sortBy, setSortBy] = useState<"date" | "amount">("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadingTxns, setLoadingTxns] = useState(false);
  // E3: a re-filed row refetches the list + breakdowns in place (no spinner): only a new card,
  // period or retry shows the spinner.
  const [txnVersion, setTxnVersion] = useState(0);
  const spinnerKey = useRef<string | null>(null);
  // E6: the active EMI plans on this card (and its billing-group cards).
  const [plans, setPlans] = useState<EmiPlan[] | null>(null);
  const [plansError, setPlansError] = useState<string | null>(null);
  const [plansAttempt, setPlansAttempt] = useState(0);
  const period: CardPeriodKey = periodChoice ?? defaultCardPeriod(card?.latest_statement);

  // ── Data Fetching ──────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getJson("/api/cards");
        const cards: CardInfo[] = data.cards || [];
        const found = cards.find((c) => c.id === Number(cardId)) || null;
        if (cancelled) return;
        setCard(found);
        setGroup(
          found?.billing_group ? cards.filter((c) => c.billing_group === found.billing_group) : []
        );
        setCardError(null);
      } catch (e) {
        // An outage is an error with Retry, not "Card not found" (E2 M11).
        if (!cancelled) setCardError(errorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cardId, cardAttempt]);

  const query = card ? cardTransactionsQuery(period, card.latest_statement) : null;

  useEffect(() => {
    if (query === null) return;
    let cancelled = false;
    const hardKey = `${cardId}|${query}|${txnAttempt}`;
    const showSpinner = spinnerKey.current !== hardKey;
    spinnerKey.current = hardKey;
    (async () => {
      if (showSpinner) setLoadingTxns(true);
      try {
        const data = await getJson<TxnData>(
          `/api/cards/${cardId}/transactions?${query ? `${query}&` : ""}limit=500`
        );
        if (!cancelled) {
          setTxnData(data);
          setTxnError(null);
        }
      } catch (e) {
        if (!cancelled) setTxnError(errorMessage(e));
      } finally {
        if (!cancelled) setLoadingTxns(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cardId, query, txnAttempt, txnVersion]);

  // E6: EMI plans of every card on this bill (an add-on's plans show on the group's page too).
  const planCards = card ? (group.length > 1 ? group : [card]).map((c) => c.last4).join(",") : "";
  useEffect(() => {
    if (!planCards) return;
    let cancelled = false;
    Promise.all(
      planCards
        .split(",")
        .map((l4) =>
          getJson<{ plans: EmiPlan[] }>(`/api/emi-plans?card_last4=${encodeURIComponent(l4)}`)
        )
    )
      .then((answers) => {
        if (cancelled) return;
        setPlans(answers.flatMap((a) => a.plans || []));
        setPlansError(null);
      })
      .catch((e) => {
        if (!cancelled) setPlansError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [planCards, plansAttempt]);

  // ── Computed Data ──────────────────────────────────────────────────────

  // From Ingest: spending only, refunds / cashback netted inside their category, bill payments
  // and EMI principal never added; each category's merchants add up to it (E2 B3). A category with
  // only refunds in the window is money back, listed apart — not a negative spending row.
  const { categoryBreakdown, refundsOnly } = useMemo(() => {
    const split = splitCategoryBreakdown(txnData?.category_breakdown || []);
    return {
      categoryBreakdown: split.spend.map((c) => ({
        category: c.category,
        amount: c.total,
        merchants: c.merchants,
      })),
      refundsOnly: split.refundsOnly,
    };
  }, [txnData]);

  const groupedByMonth = useMemo(() => {
    if (!txnData?.transactions) return [];
    const sorted = [...txnData.transactions];
    if (sortBy === "amount") {
      sorted.sort((a, b) => {
        const diff = Math.abs(b.amount) - Math.abs(a.amount);
        return sortDir === "desc" ? diff : -diff;
      });
    } else {
      sorted.sort((a, b) => {
        const diff = new Date(b.date).getTime() - new Date(a.date).getTime();
        return sortDir === "desc" ? diff : -diff;
      });
    }

    if (sortBy === "amount") {
      return [
        {
          key: "all",
          label: `All Transactions (by ${sortDir === "desc" ? "highest" : "lowest"} amount)`,
          txns: sorted,
        },
      ];
    }

    const map = new Map<string, Transaction[]>();
    for (const txn of sorted) {
      const key = getMonthYearLabel(txn.date);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(txn);
    }
    const groups: { key: string; label: string; txns: Transaction[] }[] = [];
    for (const [label, txns] of map) {
      groups.push({ key: label, label, txns });
    }
    return groups;
  }, [txnData, sortBy, sortDir]);

  const maxCatAmount = useMemo(
    () => Math.max(...categoryBreakdown.map((c) => c.amount), 1),
    [categoryBreakdown]
  );
  const totalSpend = txnData?.total ?? 0;

  const toggleCat = (cat: string) => {
    setExpandedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  useEffect(() => {
    if (!periodOpen) return;
    const handler = () => setPeriodOpen(false);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [periodOpen]);

  // ─── Loading ────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="animate-spin text-[var(--text-muted)]" size={32} />
      </div>
    );
  }

  if (cardError) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-12">
        <Link
          href="/cards"
          className="mb-6 inline-flex items-center gap-1.5 text-sm text-[var(--foreground-secondary)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft size={16} /> Back to Cards
        </Link>
        <ErrorState
          message={cardError}
          onRetry={() => {
            setLoading(true);
            setCardAttempt((a) => a + 1);
          }}
        />
      </div>
    );
  }

  if (!card) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-12">
        <Link
          href="/cards"
          className="mb-6 inline-flex items-center gap-1.5 text-sm text-[var(--foreground-secondary)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft size={16} /> Back to Cards
        </Link>
        <div className="py-20 text-center">
          <CreditCard className="mx-auto mb-4 text-[var(--text-muted)]" size={48} />
          <p className="text-[var(--foreground-secondary)]">Card not found</p>
        </div>
      </div>
    );
  }

  // ─── Derived ────────────────────────────────────────────────────────────

  const colors = getCardColors(card.bank, card.card_name);
  const stmt = card.latest_statement;
  const nextStmtDate = getNextStatementDate(card);
  const periodOptions = cardPeriodOptions(stmt);
  const periodLabel = cardPeriodLabel(period, txnData?.unbilled_since ?? stmt?.billing_end);
  // E2 M9: a merged billing group (an add-on and its primary) is one bill: its unbilled spend and
  // its transactions are shown together on either card's page.
  const groupCards = group.length > 1 ? group : [card];
  // spends and refunds counted apart, like the Unbilled list shows them (E2 round 2)
  const groupUnbilled = unbilledSummary(groupCards);
  const otherLast4s = groupCards.filter((c) => c.id !== card.id).map((c) => c.last4);
  const billNote = sameBillNote(otherLast4s);
  const shownCount = txnData?.count ?? 0;
  const totalCount = txnData?.total_count ?? shownCount;
  // iter-18 contract 3: the status is Ingest's payment_status (decided in IST), the same
  // value the cards list shows; the local day count below only words the label (M9).
  const status = resolvePaymentStatus(card);
  const isPaid = isSettled(status);
  const isOverdue = status === "overdue";
  const notConfirmed = status === "not_confirmed";
  const daysUntilDue = stmt ? daysUntil(stmt.due_date) : null;
  const isDueSoon =
    (status === "unpaid" || status === "partial") &&
    daysUntilDue !== null &&
    daysUntilDue >= 0 &&
    daysUntilDue <= 5;
  const billed = statementAmountDue(card);
  const remaining = remainingDue(card);
  const dueEstimated = isDueDateEstimated(card);

  function getStatusBadge() {
    if (!stmt) return null;
    if (isPaid)
      return {
        label: status === "no_dues" ? "No dues" : "Paid",
        icon: CheckCircle2,
        textClass: "text-emerald-600 dark:text-emerald-400",
        bgClass: "bg-emerald-500/15",
      };
    if (isOverdue)
      return {
        label: "Overdue",
        icon: AlertTriangle,
        textClass: "text-red-600 dark:text-red-400",
        bgClass: "bg-red-500/15",
      };
    // Neutral, neither alarm nor tick: Ingest cannot tell whether this bill was paid.
    if (notConfirmed)
      return {
        label: "Not confirmed",
        icon: HelpCircle,
        textClass: "text-[var(--foreground-secondary)]",
        bgClass: "bg-white/10",
      };
    if (isDueSoon)
      return {
        label: "Due Soon",
        icon: Clock,
        textClass: "text-amber-600 dark:text-amber-400",
        bgClass: "bg-amber-500/15",
      };
    return {
      label: status === "partial" ? "Part paid" : "Unpaid",
      icon: Hourglass,
      textClass: "text-amber-600 dark:text-amber-400",
      bgClass: "bg-amber-500/15",
    };
  }

  const statusBadge = getStatusBadge();

  // ─── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      {/* Back nav */}
      <div className="flex items-center gap-3 py-4">
        <Link
          href="/cards"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--foreground-secondary)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft size={16} />
          <span>Cards</span>
        </Link>
        <div className="h-4 w-px bg-[var(--border)]" />
        <span className="truncate text-sm font-medium text-[var(--foreground)]">
          {card.card_name}
        </span>
      </div>

      {/* ── Card Banner ─────────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className={`relative overflow-hidden rounded-2xl bg-gradient-to-br p-6 sm:p-8 ${colors.bg} text-white shadow-2xl`}
      >
        {/* Metallic cross-hatch pattern */}
        <div
          className="absolute inset-0 opacity-[0.035]"
          style={{
            backgroundSize: "14px 14px",
            backgroundImage: `linear-gradient(45deg, ${colors.accent}50 1px, transparent 1px), linear-gradient(-45deg, ${colors.accent}50 1px, transparent 1px)`,
          }}
        />
        {/* Accent glow orb */}
        <div
          className="absolute -top-20 -right-20 h-56 w-56 rounded-full opacity-20 blur-[80px]"
          style={{ backgroundColor: colors.accent }}
        />

        <div className="relative z-10 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          {/* Left: Bank, Name, Card Number */}
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold tracking-[0.25em] text-white/40 uppercase">
              {card.bank}
            </p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{card.card_name}</h1>
            <div className="mt-4 flex items-center gap-2">
              {[0, 1, 2].map((g) => (
                <div key={g} className="flex gap-[3px]">
                  {[0, 1, 2, 3].map((d) => (
                    <div key={d} className="h-[5px] w-[5px] rounded-full bg-white/20" />
                  ))}
                </div>
              ))}
              <span
                className="ml-1 text-xl font-bold tracking-wider"
                style={{ color: colors.accent }}
              >
                {card.last4}
              </span>
            </div>
            {card.network && (
              <div className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.06] px-2.5 py-1">
                <CreditCard className="h-3.5 w-3.5 text-white/40" />
                <span className="text-[10px] font-medium tracking-wider text-white/50 uppercase">
                  {card.network}
                </span>
              </div>
            )}
          </div>

          {/* Right: Status badge */}
          {statusBadge && (
            <div
              className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-2 ${statusBadge.bgClass} self-start`}
            >
              <statusBadge.icon className={`h-4 w-4 ${statusBadge.textClass}`} />
              <span className={`text-sm font-semibold ${statusBadge.textClass}`}>
                {statusBadge.label}
              </span>
            </div>
          )}
        </div>
      </motion.div>

      {/* ── Statement Summary ───────────────────────────────────────────── */}
      {stmt && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.08 }}
          className="mt-4 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6"
        >
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            {/* Billed Amount */}
            <div>
              <p className="mb-1.5 text-[11px] font-medium tracking-wider text-[var(--text-muted)] uppercase">
                Billed Amount
              </p>
              {/* The bill (bank-stated, else net of cashback/refunds), not the gross total (H2). */}
              <p className="font-mono text-xl font-bold text-[var(--foreground)]">
                {autoFormatCurrency(billed)}
              </p>
              <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                {stmt.txn_count} transactions
              </p>
            </div>

            {/* Paid Amount */}
            <div>
              <p className="mb-1.5 text-[11px] font-medium tracking-wider text-[var(--text-muted)] uppercase">
                Paid Amount
              </p>
              <p className="font-mono text-xl font-bold text-emerald-700 dark:text-emerald-400">
                {card.payment ? autoFormatCurrency(card.payment.total) : autoFormatCurrency(0)}
              </p>
              {card.payment && card.payment.latest_date && (
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  Last: {formatDateShort(card.payment.latest_date)}
                </p>
              )}
            </div>

            {/* Remaining */}
            <div>
              <p className="mb-1.5 text-[11px] font-medium tracking-wider text-[var(--text-muted)] uppercase">
                Remaining
              </p>
              <p
                className={`font-mono text-xl font-bold ${
                  isPaid
                    ? "text-emerald-600 dark:text-emerald-400"
                    : isOverdue
                      ? "text-red-600 dark:text-red-400"
                      : notConfirmed
                        ? "text-[var(--foreground-secondary)]"
                        : "text-amber-600 dark:text-amber-400"
                }`}
              >
                {isPaid ? autoFormatCurrency(0) : autoFormatCurrency(remaining)}
              </p>
              {isPaid && (
                <p className="mt-1 text-[11px] text-emerald-600/70 dark:text-emerald-400/70">
                  Fully settled
                </p>
              )}
              {notConfirmed && (
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">Payment not confirmed</p>
              )}
            </div>

            {/* Due Date */}
            <div>
              <p className="mb-1.5 text-[11px] font-medium tracking-wider text-[var(--text-muted)] uppercase">
                Due Date
              </p>
              <p className="text-xl font-bold text-[var(--foreground)]">
                {formatDateShort(stmt.due_date)}
                {dueEstimated && (
                  <span
                    className="ml-1.5 align-middle text-[11px] font-medium text-[var(--text-muted)]"
                    title="Estimated from the card's usual due day; the bank's due date has not been captured"
                  >
                    est.
                  </span>
                )}
              </p>
              {daysUntilDue !== null && !isPaid && (
                <p
                  className={`mt-1 text-[11px] ${
                    isOverdue
                      ? "text-red-600 dark:text-red-400"
                      : isDueSoon
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-[var(--text-muted)]"
                  }`}
                >
                  {dueDayLabel(daysUntilDue, isOverdue)}
                </p>
              )}
              {isPaid && nextStmtDate && (
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  Next stmt: {formatDateShort(nextStmtDate)}
                </p>
              )}
            </div>
          </div>

          {/* Payment History (inline, if partial) */}
          {card.payment && card.payment.payments && card.payment.payments.length > 1 && !isPaid && (
            <div className="mt-5 border-t border-[var(--border)] pt-4">
              <div className="mb-3 flex items-center gap-2">
                <Clock size={13} className="text-[var(--text-muted)]" />
                <span className="text-xs font-medium text-[var(--foreground-secondary)]">
                  Payment History
                </span>
              </div>
              <div className="flex flex-wrap gap-3">
                {card.payment.payments.map((p, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 rounded-lg border border-emerald-500/10 bg-emerald-500/[0.06] px-3 py-1.5"
                  >
                    <span className="text-xs text-[var(--foreground-secondary)]">
                      {formatDateShort(p.date)}
                    </span>
                    <span className="font-mono text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                      {autoFormatCurrency(p.amount)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* ── Unbilled Spend ──────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12 }}
        className="mt-4 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Activity size={15} className="text-blue-400" />
            <span className="text-sm font-medium text-[var(--foreground)]">Unbilled Spend</span>
            {billNote && <span className="text-xs text-[var(--text-muted)]">{billNote}</span>}
          </div>
          <div className="text-right">
            <span className="font-mono text-lg font-bold text-[var(--foreground)]">
              {autoFormatCurrency(groupUnbilled.total)}
            </span>
            <span className="ml-2 text-xs text-[var(--text-muted)]" data-role="unbilled-count">
              {groupUnbilled.text}
            </span>
          </div>
        </div>
      </motion.div>

      {/* ── EMI plans (E6): only when this bill has an active plan ───────── */}
      {plansError ? (
        <div className="mt-4 rounded-xl border border-[var(--border)] bg-[var(--card)]">
          <ErrorState
            compact
            message={`EMI plans: ${plansError}`}
            onRetry={() => setPlansAttempt((a) => a + 1)}
          />
        </div>
      ) : plans && plans.length > 0 ? (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.14 }}
          className="mt-4"
          data-role="card-emi-plans"
        >
          <div className="mb-2 flex items-center gap-2">
            <Wallet size={14} className="text-[var(--text-muted)]" />
            <span className="text-sm font-semibold text-[var(--foreground)]">EMI plans</span>
            <span className="text-xs text-[var(--text-muted)]">({plans.length})</span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {plans.map((p, i) => (
              <EmiPlanCard key={p.plan_id ?? `${p.merchant}-${i}`} plan={p} index={i} />
            ))}
          </div>
        </motion.div>
      ) : null}

      {/* ── Period Selector ─────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.16 }}
        className="mt-8 flex items-center justify-between"
      >
        <div className="flex items-center gap-2">
          <TrendingUp size={15} className="text-[var(--text-muted)]" />
          <span className="text-sm font-semibold text-[var(--foreground)]">Spending Analysis</span>
        </div>
        <div className="relative">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setPeriodOpen((p) => !p);
            }}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--foreground-secondary)] transition-colors hover:border-[var(--foreground-secondary)]"
          >
            <Calendar size={13} className="text-[var(--text-muted)]" />
            {periodLabel}
            <ChevronDown
              size={14}
              className={`text-[var(--text-muted)] transition-transform ${periodOpen ? "rotate-180" : ""}`}
            />
          </button>
          {periodOpen && (
            <div className="absolute right-0 z-30 mt-1 w-52 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-2xl">
              {periodOptions.map((p) => (
                <button
                  key={p.key}
                  onClick={(e) => {
                    e.stopPropagation();
                    setPeriod(p.key);
                    setPeriodOpen(false);
                    setExpandedCats(new Set());
                  }}
                  className={`w-full px-4 py-2.5 text-left text-sm transition-colors ${
                    period === p.key
                      ? "bg-emerald-400/[0.06] text-emerald-600 dark:text-emerald-400"
                      : "text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)]"
                  }`}
                >
                  {p.key === "current_stmt" ? cardPeriodLabel(p.key, stmt?.billing_end) : p.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </motion.div>

      {/* ── Two-Column Layout ───────────────────────────────────────────── */}
      <div
        className="mt-4 flex flex-col gap-4 lg:flex-row"
        data-oid="cointrail.spending-analysis"
        data-role="section"
      >
        {/* Left: Transaction History (2/3) */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="min-w-0 lg:w-2/3"
          data-role="transaction-history"
          data-oid="cointrail.transaction-history"
        >
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Receipt size={14} className="text-[var(--text-muted)]" />
              <span className="text-sm font-semibold text-[var(--foreground)]">
                Transaction History
              </span>
              {txnData && (
                <span className="text-xs text-[var(--text-muted)]" data-role="history-count">
                  {totalCount > shownCount
                    ? `(showing ${shownCount} of ${totalCount})`
                    : `(${shownCount})`}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  if (sortBy === "date") {
                    setSortDir((prev) => (prev === "desc" ? "asc" : "desc"));
                  } else {
                    setSortBy("date");
                    setSortDir("desc");
                  }
                }}
                className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
                  sortBy === "date"
                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : "bg-[var(--stat-bg)] text-[var(--foreground-secondary)] hover:text-[var(--foreground)]"
                }`}
              >
                Date {sortBy === "date" ? (sortDir === "desc" ? "\u2193" : "\u2191") : ""}
              </button>
              <button
                onClick={() => {
                  if (sortBy === "amount") {
                    setSortDir((prev) => (prev === "desc" ? "asc" : "desc"));
                  } else {
                    setSortBy("amount");
                    setSortDir("desc");
                  }
                }}
                className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
                  sortBy === "amount"
                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : "bg-[var(--stat-bg)] text-[var(--foreground-secondary)] hover:text-[var(--foreground)]"
                }`}
              >
                Amount {sortBy === "amount" ? (sortDir === "desc" ? "\u2193" : "\u2191") : ""}
              </button>
            </div>
          </div>

          {loadingTxns ? (
            <div className="flex items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--card)] p-12">
              <Loader2 className="animate-spin text-[var(--text-muted)]" size={24} />
            </div>
          ) : txnError ? (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--card)]">
              <ErrorState compact message={txnError} onRetry={() => setTxnAttempt((a) => a + 1)} />
            </div>
          ) : groupedByMonth.length > 0 ? (
            <div className="space-y-4">
              {groupedByMonth.map(({ key, label, txns }) => (
                <div key={key}>
                  <div className="sticky top-0 z-10 mb-1 py-1.5">
                    <span className="text-xs font-semibold tracking-wider text-[var(--text-muted)] uppercase">
                      {label}
                    </span>
                  </div>
                  <div className="divide-y divide-[var(--border)]/50 rounded-xl border border-[var(--border)] bg-[var(--card)] px-2 py-1">
                    {txns.map((txn, i) => (
                      <TransactionRow
                        key={txn.id}
                        txn={txn}
                        index={i}
                        showCard={otherLast4s.length > 0}
                        onChanged={() => setTxnVersion((v) => v + 1)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-12 text-center">
              <Receipt className="mx-auto mb-3 text-[var(--text-muted)]" size={32} />
              <p className="text-sm text-[var(--text-muted)]">No transactions for this period</p>
            </div>
          )}
        </motion.div>

        {/* Right: Sidebar (1/3) */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="space-y-4 lg:w-1/3"
        >
          {/* Top Merchants */}
          <div
            className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
            data-role="top-merchants"
            data-oid="cointrail.top-merchants"
          >
            <div className="mb-4 flex items-center gap-2">
              <TrendingUp size={14} className="text-[var(--text-muted)]" />
              <span className="text-sm font-semibold text-[var(--foreground)]">Top Merchants</span>
            </div>

            {loadingTxns ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="animate-spin text-[var(--text-muted)]" size={18} />
              </div>
            ) : txnData?.top_merchants && txnData.top_merchants.length > 0 ? (
              <div className="space-y-2.5">
                {txnData.top_merchants.slice(0, 8).map((m, i) => (
                  <motion.div
                    key={m.name}
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.3 + i * 0.04 }}
                    className="flex items-center gap-3"
                  >
                    <span className="w-5 text-right text-xs font-bold text-[var(--text-muted)] tabular-nums">
                      {i + 1}
                    </span>
                    <img
                      src={getMerchantLogoUrl(m.name) ?? undefined}
                      alt=""
                      className="h-7 w-7 flex-shrink-0 rounded-lg bg-[var(--input)] object-cover"
                      onError={(e) => {
                        const el = e.target as HTMLImageElement;
                        el.style.display = "none";
                        const parent = el.parentElement;
                        if (parent && !parent.querySelector(".fallback-icon")) {
                          const fallback = document.createElement("div");
                          fallback.className =
                            "fallback-icon w-7 h-7 rounded-lg bg-[var(--input)] flex items-center justify-center text-[var(--text-muted)] text-[10px] font-semibold flex-shrink-0";
                          fallback.textContent = m.name.charAt(0).toUpperCase();
                          parent.insertBefore(fallback, el);
                        }
                      }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-[var(--foreground)]">{m.name}</div>
                      <div className="text-[10px] text-[var(--text-muted)]">
                        {m.count} txn{m.count !== 1 ? "s" : ""}
                      </div>
                    </div>
                    <span className="flex-shrink-0 font-mono text-sm font-semibold text-[var(--foreground)]">
                      {autoFormatCurrency(m.total)}
                    </span>
                  </motion.div>
                ))}
              </div>
            ) : (
              <p className="py-4 text-center text-sm text-[var(--text-muted)]">No merchant data</p>
            )}
          </div>

          {/* Category Breakdown */}
          <div
            className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
            data-role="category-breakdown"
            data-oid="cointrail.category-breakdown"
          >
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Banknote size={14} className="text-[var(--text-muted)]" />
                <span className="text-sm font-semibold text-[var(--foreground)]">
                  Category Breakdown
                </span>
              </div>
              {categoryBreakdown.length > 0 && (
                <span className="font-mono text-xs text-[var(--text-muted)]">
                  {autoFormatCurrency(totalSpend)}
                </span>
              )}
            </div>

            {loadingTxns ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="animate-spin text-[var(--text-muted)]" size={18} />
              </div>
            ) : categoryBreakdown.length > 0 ? (
              <div className="space-y-3">
                {categoryBreakdown.map(({ category, amount, merchants }, i) => {
                  const color = getCatColor(category);
                  const pct = maxCatAmount > 0 ? (amount / maxCatAmount) * 100 : 0;
                  const isExpanded = expandedCats.has(category);

                  return (
                    <motion.div
                      key={category}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.3 + i * 0.03 }}
                    >
                      <button
                        onClick={() => toggleCat(category)}
                        className="group w-full text-left"
                      >
                        <div className="mb-1.5 flex items-center justify-between">
                          <div className="flex min-w-0 items-center gap-2">
                            <div
                              className="h-2.5 w-2.5 flex-shrink-0 rounded-full"
                              style={{ backgroundColor: color }}
                            />
                            <span className="truncate text-sm text-[var(--foreground)]">
                              {category}
                            </span>
                          </div>
                          <span className="flex-shrink-0 font-mono text-sm font-medium text-[var(--foreground)]">
                            {autoFormatCurrency(amount)}
                          </span>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-[var(--stat-bg)]">
                          <motion.div
                            initial={{ width: 0 }}
                            animate={{ width: `${pct}%` }}
                            transition={{ duration: 0.6, delay: 0.3 + i * 0.03 }}
                            className="h-full rounded-full"
                            style={{ backgroundColor: color }}
                          />
                        </div>
                      </button>

                      {isExpanded && merchants.length > 0 && (
                        <div
                          className="mt-2 ml-4 space-y-1 border-l-2 pl-3"
                          style={{ borderColor: `${color}30` }}
                        >
                          {merchants.map((m) => (
                            <div
                              key={m.name}
                              className="flex items-center justify-between py-1 text-xs"
                            >
                              <span className="truncate text-[var(--foreground-secondary)]">
                                {m.name}
                              </span>
                              <span className="ml-2 flex-shrink-0 font-mono text-[var(--foreground)]">
                                {autoFormatCurrency(m.total)}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </motion.div>
                  );
                })}
              </div>
            ) : (
              <p className="py-4 text-center text-sm text-[var(--text-muted)]">
                No spending data for this period
              </p>
            )}
            {!loadingTxns && refundsOnly.length > 0 && (
              <div
                className="mt-3 space-y-1 border-t border-[var(--border)] pt-3"
                data-role="refunds-only"
              >
                {refundsOnly.map((r) => (
                  <div key={r.category} className="flex items-center justify-between text-xs">
                    <span className="truncate text-[var(--foreground-secondary)]">
                      {r.category} refunds{" "}
                      <span className="text-[10px] text-[var(--text-muted)]">
                        {netRefundNote()}
                      </span>
                    </span>
                    <span className="ml-2 flex-shrink-0 font-mono text-emerald-700 dark:text-emerald-400">
                      +{autoFormatCurrency(r.refunded)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </motion.div>
      </div>
    </div>
  );
}
