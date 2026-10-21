"use client";

import React, { useState, useEffect, useRef } from "react";

import { formatIndianNumber } from "@/lib/formatters";
import { getMerchantLogoUrl } from "@/lib/logos";
import { getCategoryBadgeColor } from "@/lib/constants";
import { toLocalISODate } from "@/lib/dates";
import {
  cancelPayload,
  chargedAfterCancelText,
  frequencyLabel,
  isValidDay,
  lastChargeAmount,
  monthlyEquiv,
  type ChargeAfterCancel,
} from "@/lib/recurring";
import { createSingleFlight } from "@/lib/category-edit";
import { isEmiPlanItem, type EmiPlan } from "@/lib/emi";
import { HttpError, errorMessage, getJson } from "@/lib/http";
import { EmiPlanCard } from "@/components/shared/EmiPlanCard";
import {
  Ban,
  CreditCard,
  Repeat,
  Zap,
  Calendar,
  TrendingUp,
  Loader2,
  AlertCircle,
  AlertTriangle,
  RefreshCw,
  Undo2,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────

interface RecurringItem {
  /** Ingest's key for the series (iteration 203 E5): what a cancellation is stored against. */
  series_key: string;
  status?: string;
  /** Set on an EMI plan row (iteration 203 E6). */
  emi_plan?: EmiPlan | null;
  merchant: string;
  category: string;
  type: "subscription" | "emi" | "recurring";
  frequency?: "monthly" | "yearly" | "biennial";
  avg_amount: number;
  /** The latest charge (iteration 203 E5 round 2). */
  last_amount?: number | null;
  occurrences: number;
  months: string[];
  last_date: string | null;
  next_expected: string | null;
  total_paid: number;
  logo_domain: string | null;
  card_last4: string | null;
  payments_done: number | null;
  total_installments: number | null;
  product_name: string | null;
  display_name: string | null;
}

/** A series the owner marked cancelled (Ingest /recurring/detect `cancelled`, iteration 203 E5). */
interface CancelledItem extends Partial<RecurringItem> {
  series_key: string;
  merchant: string;
  display_name?: string | null;
  cancelled_on: string;
  cancellation_id: number;
  /** False when the series no longer shows up in the transactions at all. */
  detected?: boolean;
  charged_after_cancel?: ChargeAfterCancel[];
}

// ─── Helpers ──────────────────────────────────────────────
// Monthly equivalents and "/mo" / "/yr" / "/2yr" labels come from lib/recurring, shared with the
// Expenses Overview so both pages agree (iteration 203 E2 M6).
const getFrequencyLabel = frequencyLabel;

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "\u2014";
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

type LogoSource = { merchant: string; logo_domain?: string | null };

function getLogoUrl(item: LogoSource): string | null {
  if (item.logo_domain) {
    return `https://www.google.com/s2/favicons?domain=${item.logo_domain}&sz=64`;
  }
  return getMerchantLogoUrl(item.merchant, 64);
}

// ─── Section Components ───────────────────────────────────

function SectionHeader({
  icon: Icon,
  title,
  count,
  total,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  count: number;
  total: number;
}) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Icon className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
        <h2 className="text-lg font-semibold text-[var(--foreground)]">{title}</h2>
        <span className="text-sm text-[var(--text-muted)]">({count})</span>
      </div>
      <div className="text-sm text-[var(--foreground-secondary)]">
        Monthly:{" "}
        <span className="font-medium text-emerald-700 dark:text-emerald-400">
          {formatIndianNumber(total)}
        </span>
      </div>
    </div>
  );
}

function MerchantLogo({ item }: { item: LogoSource }) {
  const logoUrl = getLogoUrl(item);
  const [imgError, setImgError] = useState(false);

  if (!logoUrl || imgError) {
    return (
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--stat-bg)] text-sm font-bold text-[var(--text-muted)]">
        {item.merchant.charAt(0).toUpperCase()}
      </div>
    );
  }

  return (
    <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--card-hover)]">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={logoUrl}
        alt={item.merchant}
        className="h-6 w-6 object-contain"
        onError={() => setImgError(true)}
      />
    </div>
  );
}

function writeError(e: unknown, what: string): string {
  if (e instanceof HttpError && e.status < 500) return `Couldn't ${what}: ${e.message}.`;
  return `Couldn't ${what} — the data service did not answer. Nothing was changed; try again.`;
}

/**
 * "Mark as cancelled" (iteration 203 E5): asks for the date (today by default), then Ingest takes
 * the series out of the active list and every monthly total; a charge after the date is flagged.
 */
function CancelControl({ item, onDone }: { item: RecurringItem; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState(() => toLocalISODate(new Date()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const single = useRef(createSingleFlight<unknown>());
  const name = item.display_name || item.merchant;

  const confirm = () => {
    if (!isValidDay(day)) {
      setError("Pick the date you cancelled it.");
      return;
    }
    const p = single.current(() =>
      getJson("/api/recurring/cancellations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cancelPayload(item, day)),
      })
    );
    if (!p) return; // already sending: one click, one cancellation
    setBusy(true);
    setError(null);
    p.then(() => {
      setOpen(false);
      onDone();
    })
      .catch((e) => setError(writeError(e, "cancel it")))
      .finally(() => setBusy(false));
  };

  if (!open) {
    return (
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={() => {
            setError(null);
            setOpen(true);
          }}
          data-role="mark-cancelled"
          aria-label={`Mark ${name} as cancelled`}
          className="flex min-h-[28px] items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[var(--card-hover)] hover:text-[var(--foreground)]"
        >
          <Ban className="h-3 w-3" aria-hidden /> Mark as cancelled
        </button>
      </div>
    );
  }
  return (
    <div
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] bg-[var(--stat-bg)] p-2.5"
      data-role="cancel-confirm"
    >
      <label className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--foreground-secondary)]">
        Cancelled {name} on
        <input
          type="date"
          value={day}
          onChange={(e) => setDay(e.target.value)}
          aria-label="Cancellation date"
          className="rounded-md border border-[var(--border)] bg-[var(--input)] px-2 py-0.5 text-[11px] text-[var(--foreground)]"
        />
      </label>
      <p className="text-[10px] text-[var(--text-muted)]">
        It leaves the monthly totals; a charge after this date is flagged here and in the daily
        digest.
      </p>
      {error && (
        <p role="alert" className="text-[11px] text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={busy}
          className="rounded-md px-2.5 py-1 text-[11px] text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)] disabled:opacity-50"
        >
          Keep it
        </button>
        <button
          type="button"
          onClick={confirm}
          disabled={busy}
          data-role="confirm-cancelled"
          className="flex items-center gap-1 rounded-md bg-red-600 px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          Mark cancelled
        </button>
      </div>
    </div>
  );
}

/** A cancelled series, with Undo and a warning if it charged after the date (iteration 203 E5). */
function CancelledCard({
  item,
  index,
  onDone,
}: {
  item: CancelledItem;
  index: number;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const single = useRef(createSingleFlight<unknown>());
  const name = item.display_name || item.merchant;
  const warning = chargedAfterCancelText(item);
  // what it last cost, not the average over older, cheaper charges (E5 round 2)
  const lastCharge = lastChargeAmount(item);

  const undo = () => {
    const p = single.current(() =>
      getJson(`/api/recurring/cancellations/${item.cancellation_id}`, { method: "DELETE" })
    );
    if (!p) return;
    setBusy(true);
    setError(null);
    p.then(() => onDone())
      .catch((e) => setError(writeError(e, "undo it")))
      .finally(() => setBusy(false));
  };

  return (
    <div
      className="animate-fade-in rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 opacity-90"
      style={{ animationDelay: `${index * 50}ms` }}
      data-role="cancelled-item"
      data-series-key={item.series_key}
    >
      <div className="flex items-start gap-3">
        <MerchantLogo item={{ merchant: item.merchant, logo_domain: item.logo_domain }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-[var(--foreground)] line-through decoration-[var(--text-muted)]">
              {name}
            </h3>
            {lastCharge !== null && item.type && (
              <span
                className="text-sm font-semibold whitespace-nowrap text-[var(--text-muted)]"
                title="The last charge before you cancelled it"
              >
                {formatIndianNumber(lastCharge)}
                <span className="ml-1 text-[10px]">
                  {frequencyLabel({
                    type: item.type,
                    frequency: item.frequency,
                    avg_amount: lastCharge,
                    months: item.months,
                  })}
                </span>
              </span>
            )}
          </div>
          <div className="mt-1 text-[10px] text-[var(--text-muted)]">
            Cancelled on {formatDate(item.cancelled_on)}
            {item.last_date && ` · last charged ${formatDate(item.last_date)}`}
            {item.detected === false && " · no longer seen in your transactions"}
          </div>
          {warning && (
            <p
              role="alert"
              className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-red-700 dark:text-red-400"
              data-role="charged-after-cancel"
            >
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden /> {warning}
            </p>
          )}
          {error && (
            <p role="alert" className="mt-2 text-[11px] text-red-700 dark:text-red-400">
              {error}
            </p>
          )}
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              onClick={undo}
              disabled={busy}
              data-role="undo-cancel"
              aria-label={`Undo cancelling ${name}`}
              className="flex min-h-[28px] items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-[var(--card-hover)] disabled:opacity-50 dark:text-emerald-400"
            >
              {busy ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              ) : (
                <Undo2 className="h-3 w-3" aria-hidden />
              )}
              Undo
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SubscriptionCard({
  item,
  index,
  footer,
}: {
  item: RecurringItem;
  index: number;
  footer?: React.ReactNode;
}) {
  return (
    <div
      className="animate-fade-in rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-colors hover:border-emerald-500/20"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <div className="flex items-start gap-3">
        <MerchantLogo item={item} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-[var(--foreground)]">
              {item.display_name || item.merchant}
            </h3>
            <span className="text-sm font-semibold whitespace-nowrap text-emerald-700 dark:text-emerald-400">
              {formatIndianNumber(item.avg_amount)}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full border px-1.5 py-0 text-[10px] ${getCategoryBadgeColor(item.category)}`}
            >
              {item.category}
            </span>
            <span className="text-[10px] text-[var(--text-muted)]">
              {item.occurrences} payments
            </span>
            {item.card_last4 && (
              <span className="flex items-center gap-0.5 text-[10px] text-[var(--text-muted)]">
                <CreditCard className="h-2.5 w-2.5" />
                {item.card_last4}
              </span>
            )}
          </div>
          <div className="mt-2 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
            <span>Total: {formatIndianNumber(item.total_paid)}</span>
            {item.next_expected && (
              <span className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                Next: {formatDate(item.next_expected)}
              </span>
            )}
          </div>
        </div>
      </div>
      {footer}
    </div>
  );
}

function EmiCard({
  item,
  index,
  footer,
}: {
  item: RecurringItem;
  index: number;
  footer?: React.ReactNode;
}) {
  return (
    <div
      className="animate-fade-in rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-colors hover:border-red-500/20"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <div className="flex items-start gap-3">
        <MerchantLogo item={item} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-[var(--foreground)]">
              {item.display_name || item.merchant}
            </h3>
            <span className="text-sm font-semibold whitespace-nowrap text-red-700 dark:text-red-400">
              {formatIndianNumber(item.avg_amount)}
              <span className="ml-1 text-[10px] text-[var(--text-muted)]">
                {getFrequencyLabel(item)}
              </span>
            </span>
          </div>

          {/* Progress indicator */}
          <div className="mt-2">
            <div className="mb-1 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
              <span className="flex items-center gap-1.5">
                {item.payments_done && item.total_installments ? (
                  <>
                    <span className="font-mono font-semibold text-[var(--foreground)]">
                      {item.payments_done}/{item.total_installments}
                    </span>{" "}
                    paid
                  </>
                ) : (
                  <>{item.occurrences} EMIs paid</>
                )}
                {item.card_last4 && (
                  <span className="ml-1 flex items-center gap-0.5">
                    <CreditCard className="h-2.5 w-2.5" />
                    {item.card_last4}
                  </span>
                )}
              </span>
              <span>Total: {formatIndianNumber(item.total_paid)}</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--stat-bg)]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-red-500 to-red-400 transition-all"
                style={{
                  width:
                    item.payments_done && item.total_installments
                      ? `${Math.min((item.payments_done / item.total_installments) * 100, 100)}%`
                      : `${Math.min(item.occurrences * 8.33, 100)}%`,
                }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
              <span>{item.months[0]} started</span>
              {item.next_expected ? (
                <span className="flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  Next: {formatDate(item.next_expected)}
                </span>
              ) : (
                item.payments_done &&
                item.total_installments &&
                item.payments_done >= item.total_installments && (
                  <span className="font-medium text-emerald-700 dark:text-emerald-400">
                    Completed
                  </span>
                )
              )}
            </div>
          </div>
        </div>
      </div>
      {footer}
    </div>
  );
}

function RecurringCard({
  item,
  index,
  footer,
}: {
  item: RecurringItem;
  index: number;
  footer?: React.ReactNode;
}) {
  return (
    <div
      className="animate-fade-in rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-colors hover:border-yellow-500/20"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <div className="flex items-start gap-3">
        <MerchantLogo item={item} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-[var(--foreground)]">
              {item.merchant}
            </h3>
            <span className="text-sm font-semibold whitespace-nowrap text-yellow-700 dark:text-yellow-400">
              ~{formatIndianNumber(item.avg_amount)}
              <span className="ml-1 text-[10px] text-[var(--text-muted)]">
                {getFrequencyLabel(item)}
              </span>
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full border px-1.5 py-0 text-[10px] ${getCategoryBadgeColor(item.category)}`}
            >
              {item.category}
            </span>
            <span className="text-[10px] text-[var(--text-muted)]">
              {item.occurrences} payments &middot; {item.months.length} months
            </span>
          </div>
          <div className="mt-2 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
            <span>Total: {formatIndianNumber(item.total_paid)}</span>
            <span>Last: {formatDate(item.last_date)}</span>
          </div>
        </div>
      </div>
      {footer}
    </div>
  );
}

// ─── Summary Stats ────────────────────────────────────────

function SummaryBar({
  subscriptions,
  emis,
  recurring,
}: {
  subscriptions: RecurringItem[];
  emis: RecurringItem[];
  recurring: RecurringItem[];
}) {
  const subTotal = subscriptions.reduce((s, i) => s + monthlyEquiv(i), 0);
  const emiTotal = emis.reduce((s, i) => s + monthlyEquiv(i), 0);
  const recTotal = recurring.reduce((s, i) => s + monthlyEquiv(i), 0);
  const grandTotal = subTotal + emiTotal + recTotal;

  return (
    <div className="animate-fade-in mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
      {[
        {
          label: "Total Monthly",
          value: grandTotal,
          color: "text-emerald-700 dark:text-emerald-400",
          icon: TrendingUp,
        },
        {
          label: "Subscriptions",
          value: subTotal,
          color: "text-emerald-700 dark:text-emerald-400",
          icon: Zap,
        },
        {
          label: "EMIs",
          value: emiTotal,
          color: "text-red-700 dark:text-red-400",
          icon: CreditCard,
        },
        {
          label: "Recurring",
          value: recTotal,
          color: "text-yellow-700 dark:text-yellow-400",
          icon: Repeat,
        },
      ].map((stat) => (
        <div
          key={stat.label}
          className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-3"
        >
          <div className="mb-1 flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
            <stat.icon className="h-3 w-3" />
            {stat.label}
          </div>
          <div className={`text-lg font-bold ${stat.color}`}>{formatIndianNumber(stat.value)}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────

export default function RecurringExpenses() {
  const [items, setItems] = useState<RecurringItem[]>([]);
  // Series the owner cancelled (iteration 203 E5): Ingest reports them apart from the active list,
  // so none of the totals below include them.
  const [cancelled, setCancelled] = useState<CancelledItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // getJson throws on the route's 502, so an Ingest outage shows this page's error + Retry
  // instead of "No recurring expenses detected" (iteration 203 E2 M11). After a cancel / undo the
  // page refetches without the full-page spinner.
  const fetchData = async (initial = true) => {
    if (initial) setLoading(true);
    setError(null);
    try {
      const data = await getJson("/api/recurring/detect");
      setItems(data.recurring || []);
      setCancelled(data.cancelled || []);
    } catch (e) {
      setError(errorMessage(e));
      console.error(e);
    } finally {
      setLoading(false);
    }
  };
  const refresh = () => fetchData(false);

  useEffect(() => {
    fetchData();
  }, []);

  const subscriptions = items.filter((i) => i.type === "subscription");
  const emis = items.filter((i) => i.type === "emi");
  const recurring = items.filter((i) => i.type === "recurring");
  const cancelControl = (item: RecurringItem) => <CancelControl item={item} onDone={refresh} />;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-emerald-700 dark:text-emerald-400" />
        <span className="ml-2 text-sm text-[var(--foreground-secondary)]">
          Analyzing transactions...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-20">
        <AlertCircle className="h-8 w-8 text-red-700 dark:text-red-400" />
        <p className="text-sm text-[var(--foreground-secondary)]">{error}</p>
        <button
          onClick={() => fetchData()}
          className="flex items-center gap-1 text-sm text-emerald-700 hover:text-emerald-300 dark:text-emerald-400"
        >
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      </div>
    );
  }

  if (items.length === 0 && cancelled.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-20">
        <Repeat className="h-8 w-8 text-[var(--text-muted)]" />
        <p className="text-sm text-[var(--text-muted)]">No recurring expenses detected</p>
        <p className="text-xs text-[var(--text-muted)]">Need at least 3 months of data</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Summary */}
      <SummaryBar subscriptions={subscriptions} emis={emis} recurring={recurring} />

      {/* Subscriptions */}
      {subscriptions.length > 0 && (
        <section>
          <SectionHeader
            icon={Zap}
            title="Subscriptions"
            count={subscriptions.length}
            total={subscriptions.reduce((s, i) => s + monthlyEquiv(i), 0)}
          />
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {subscriptions.map((item, i) => (
              <SubscriptionCard
                key={item.series_key || item.merchant}
                item={item}
                index={i}
                footer={cancelControl(item)}
              />
            ))}
          </div>
        </section>
      )}

      {/* EMIs */}
      {emis.length > 0 && (
        <section>
          <SectionHeader
            icon={CreditCard}
            title="EMIs & Loans"
            count={emis.length}
            total={emis.reduce((s, i) => s + monthlyEquiv(i), 0)}
          />
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {/* E6: an EMI plan is one item with its purchase and full monthly payment. */}
            {emis.map((item, i) =>
              isEmiPlanItem(item) ? (
                <EmiPlanCard
                  key={item.series_key}
                  plan={item.emi_plan!}
                  index={i}
                  footer={cancelControl(item)}
                />
              ) : (
                <EmiCard
                  key={item.series_key || item.merchant}
                  item={item}
                  index={i}
                  footer={cancelControl(item)}
                />
              )
            )}
          </div>
        </section>
      )}

      {/* Other Recurring */}
      {recurring.length > 0 && (
        <section>
          <SectionHeader
            icon={Repeat}
            title="Other Recurring"
            count={recurring.length}
            total={recurring.reduce((s, i) => s + monthlyEquiv(i), 0)}
          />
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {recurring.map((item, i) => (
              <RecurringCard
                key={item.series_key || item.merchant}
                item={item}
                index={i}
                footer={cancelControl(item)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Cancelled (E5): out of every total above; Undo puts a series back. */}
      {cancelled.length > 0 && (
        <section data-role="cancelled-section">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Ban className="h-5 w-5 text-[var(--text-muted)]" />
              <h2 className="text-lg font-semibold text-[var(--foreground)]">Cancelled</h2>
              <span className="text-sm text-[var(--text-muted)]">({cancelled.length})</span>
            </div>
            <span className="text-xs text-[var(--text-muted)]">not in the monthly totals</span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {cancelled.map((item, i) => (
              <CancelledCard key={item.series_key} item={item} index={i} onDone={refresh} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
