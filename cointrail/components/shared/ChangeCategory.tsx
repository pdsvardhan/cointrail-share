"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Search, Tag, X } from "lucide-react";
import { autoFormatCurrency } from "@/lib/formatters";
import { getCategoryColor } from "@/lib/constants";
import { HttpError, getJson } from "@/lib/http";
import {
  buildSaveRequest,
  canSave,
  createAfterSaveRefresh,
  createSingleFlight,
  groupChoices,
  payeeOffer,
  saveSummary,
  type CategoryChoice,
  type PayeeInfo,
  type SaveResponse,
  type SaveSummary,
} from "@/lib/category-edit";
import { useDataVersion } from "@/components/shared/DataVersion";

/**
 * "Change category" on a transaction row (iteration 203 E3). Opens a chooser: pick a category,
 * optionally "Also file all payments to <payee> under it" (a payee rule, E9 — only on request,
 * never learned silently), save. On success the page's data version is bumped so every total
 * refetches in place; the chooser shows what the save did, including the rule it created.
 */

export interface Choices {
  categories: CategoryChoice[];
  owner_categories: string[];
}

// One fetch of the category list per page load; a failure is not cached.
let choicesRequest: Promise<Choices> | null = null;
export function loadCategoryChoices(): Promise<Choices> {
  if (!choicesRequest) {
    choicesRequest = getJson<Choices>("/api/category-choices").catch((e) => {
      choicesRequest = null;
      throw e;
    });
  }
  return choicesRequest;
}

function saveError(e: unknown): string {
  if (e instanceof HttpError && e.status < 500) return `Couldn't save: ${e.message}.`;
  return "Couldn't save — the data service did not answer. Nothing was changed; try again.";
}

export function ChangeCategoryButton({
  txnId,
  title,
  category,
  amount,
  onChanged,
}: {
  txnId: number;
  title: string;
  category: string | null;
  /** Signed (negative = money back), shown in the chooser's header. */
  amount?: number;
  /** Called after a successful save, besides the page-wide refresh. */
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const afterSave = useRef(createAfterSaveRefresh());
  const { bump } = useDataVersion();
  // The page refreshes when the chooser closes after a save, not while it is open (see
  // createAfterSaveRefresh): refreshing first would unmount this row and its confirmation.
  const close = () => {
    setOpen(false);
    afterSave.current.close(bump, onChanged);
  };
  return (
    <>
      <button
        type="button"
        data-role="change-category"
        aria-label={`Change category of ${title}`}
        title="Change category"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-[var(--text-muted)] transition-colors hover:bg-[var(--card-hover)] hover:text-[var(--foreground)] focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:outline-none"
      >
        <Tag className="h-3.5 w-3.5" aria-hidden />
      </button>
      {open && (
        <CategoryDialog
          txnId={txnId}
          title={title}
          category={category}
          amount={amount}
          onClose={close}
          onSaved={() => afterSave.current.saved()}
        />
      )}
    </>
  );
}

function CategoryDialog({
  txnId,
  title,
  category,
  amount,
  onClose,
  onSaved,
}: {
  txnId: number;
  title: string;
  category: string | null;
  amount?: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [choices, setChoices] = useState<Choices | null>(null);
  const [choicesError, setChoicesError] = useState<string | null>(null);
  const [choicesAttempt, setChoicesAttempt] = useState(0);
  const [payee, setPayee] = useState<PayeeInfo | null>(null);
  const [payeeFailed, setPayeeFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [apply, setApply] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SaveSummary | null>(null);
  const single = useRef(createSingleFlight<SaveResponse>());
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    loadCategoryChoices()
      .then((c) => {
        if (!cancelled) {
          setChoices(c);
          setChoicesError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setChoicesError("Couldn't load the categories.");
      });
    return () => {
      cancelled = true;
    };
  }, [choicesAttempt]);

  useEffect(() => {
    let cancelled = false;
    getJson<PayeeInfo>(`/api/transactions/${txnId}/payee`)
      .then((p) => {
        if (!cancelled) setPayee(p);
      })
      .catch(() => {
        if (!cancelled) setPayeeFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [txnId]);

  useEffect(() => {
    searchRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  const groups = useMemo(
    () => (choices ? groupChoices(choices.categories, choices.owner_categories, query) : []),
    [choices, query]
  );
  const offer = payeeOffer(payee);
  const owner = new Set(choices?.owner_categories ?? []);

  const save = () => {
    const askedForRule = apply && offer.canApply;
    if (!picked || !canSave(category, picked, askedForRule)) return;
    const req = buildSaveRequest(txnId, picked, askedForRule);
    const p = single.current(() => getJson<SaveResponse>(req.url, req.init));
    if (!p) return; // a save is already in flight: one change, one request
    setSaving(true);
    setError(null);
    p.then((res) => {
      setResult(saveSummary(res, askedForRule));
      onSaved();
    })
      .catch((e) => setError(saveError(e)))
      .finally(() => setSaving(false));
  };

  const dialog = (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center"
      onClick={(e) => {
        e.stopPropagation();
        if (!saving) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={`cat-dialog-${txnId}`}
        data-role="category-dialog"
        className="glass-card flex max-h-[88vh] w-full max-w-md flex-col overflow-hidden rounded-b-none sm:mx-4 sm:rounded-b-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <h3
              id={`cat-dialog-${txnId}`}
              className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase"
            >
              Change category
            </h3>
            <p className="mt-0.5 truncate text-xs text-[var(--foreground-secondary)]">{title}</p>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
              {typeof amount === "number" && (
                <span className="font-mono">
                  {amount < 0 ? "+" : ""}
                  {autoFormatCurrency(Math.abs(amount))} ·{" "}
                </span>
              )}
              now <span style={{ color: getCategoryColor(category || "") }}>{category || "—"}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            aria-label="Close"
            className="rounded-lg p-1.5 transition-colors hover:bg-[var(--card-hover)] disabled:opacity-40"
          >
            <X className="h-4 w-4 text-[var(--text-muted)]" />
          </button>
        </div>

        {result ? (
          <div className="space-y-3 px-5 py-5" data-role="category-saved" aria-live="polite">
            <p className="flex items-center gap-2 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
              <Check className="h-4 w-4" aria-hidden /> {result.headline}
            </p>
            {result.rule && (
              <p className="text-xs text-[var(--foreground-secondary)]" data-role="rule-created">
                {result.rule}
              </p>
            )}
            {result.refused && (
              <p className="text-xs text-amber-700 dark:text-amber-400">{result.refused}</p>
            )}
            <p className="text-[11px] text-[var(--text-muted)]">
              The totals on this page refresh when you close this. Payee rules are listed under
              Expenses → Categories → Payee rules.
            </p>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg bg-[var(--accent-blue)] px-4 py-1.5 text-xs font-semibold text-white"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="px-5 pt-3">
              <label className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--input)] px-3 py-1.5">
                <Search className="h-3.5 w-3.5 text-[var(--text-muted)]" aria-hidden />
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find a category"
                  aria-label="Find a category"
                  className="w-full bg-transparent text-xs text-[var(--foreground)] placeholder:text-[var(--text-muted)] focus:outline-none"
                />
              </label>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
              {choicesError ? (
                <div className="py-6 text-center text-xs text-[var(--foreground-secondary)]">
                  {choicesError}{" "}
                  <button
                    type="button"
                    className="font-semibold text-emerald-700 dark:text-emerald-400"
                    onClick={() => {
                      setChoicesError(null);
                      setChoicesAttempt((a) => a + 1);
                    }}
                  >
                    Retry
                  </button>
                </div>
              ) : !choices ? (
                <div className="flex justify-center py-6">
                  <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
                </div>
              ) : groups.length === 0 ? (
                <div className="py-6 text-center text-xs text-[var(--text-muted)]">
                  No category matches &ldquo;{query}&rdquo;
                </div>
              ) : (
                <div className="space-y-3">
                  {groups.map((g) => (
                    <div key={g.group} role="group" aria-label={g.label}>
                      <div className="mb-1.5 flex items-baseline gap-2">
                        <span className="text-[10px] font-bold tracking-wider text-[var(--text-muted)] uppercase">
                          {g.label}
                        </span>
                        <span className="text-[10px] text-[var(--text-muted)]">{g.note}</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {g.names.map((name) => {
                          const isPicked = picked === name;
                          const isCurrent = category === name;
                          return (
                            <button
                              type="button"
                              key={name}
                              aria-pressed={isPicked}
                              onClick={() => setPicked(name)}
                              className={`min-h-[28px] rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                                isPicked
                                  ? "border-transparent bg-[var(--accent-blue)] text-white"
                                  : "border-[var(--border)] text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)] hover:text-[var(--foreground)]"
                              }`}
                            >
                              {name}
                              {isCurrent && <span className="ml-1 opacity-70">(now)</span>}
                              {owner.has(name) && !isPicked && (
                                <span className="ml-1 text-[9px] text-emerald-700 dark:text-emerald-400">
                                  new
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2 border-t border-[var(--border)] px-5 py-3">
              {offer.canApply ? (
                <label className="flex cursor-pointer items-start gap-2 text-xs text-[var(--foreground-secondary)]">
                  <input
                    type="checkbox"
                    checked={apply}
                    onChange={(e) => setApply(e.target.checked)}
                    className="mt-0.5 h-4 w-4 accent-[var(--accent-blue)]"
                    data-role="apply-to-payee"
                  />
                  <span>
                    Also apply to all payments to{" "}
                    <span className="font-semibold text-[var(--foreground)]">{offer.label}</span> (
                    {offer.keyTypeLabel}, {offer.count} payment{offer.count === 1 ? "" : "s"})
                    <span className="block text-[10px] text-[var(--text-muted)]">
                      Past and future payments; ones you re-filed by hand keep theirs.
                      {offer.existingRule &&
                        ` Your rule files this payee under ${offer.existingRule.category} now.`}
                    </span>
                  </span>
                </label>
              ) : offer.refused ? (
                <p className="text-[10px] text-[var(--text-muted)]">
                  Only this payment can change: {offer.refused}.
                </p>
              ) : payeeFailed ? (
                <p className="text-[10px] text-[var(--text-muted)]">
                  Couldn&rsquo;t check the payee, so only this payment can change.
                </p>
              ) : null}
              {error && (
                <p role="alert" className="text-xs text-red-700 dark:text-red-400">
                  {error}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={saving}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium text-[var(--foreground-secondary)] transition-colors hover:bg-[var(--card-hover)] disabled:opacity-40"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={save}
                  disabled={saving || !canSave(category, picked, apply && offer.canApply)}
                  data-role="save-category"
                  className="flex items-center gap-1.5 rounded-lg bg-[var(--accent-blue)] px-4 py-1.5 text-xs font-semibold text-white transition-opacity disabled:opacity-40"
                >
                  {saving && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
                  Save
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
}
