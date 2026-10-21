"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ListChecks, Loader2, Trash2, X } from "lucide-react";
import { getCategoryColor } from "@/lib/constants";
import { errorMessage, getJson } from "@/lib/http";
import { createSingleFlight, ruleKeyLabel, type PayeeRule } from "@/lib/category-edit";
import { ErrorState } from "@/components/shared/ErrorState";
import { useDataVersion } from "@/components/shared/DataVersion";

/**
 * The payee rules the owner made (iteration 203 E9): each says "every payment to <payee> goes to
 * <category>". Rules are only ever created from a transaction's "change category" chooser with
 * "Also apply to all payments to …" ticked; here they can be read and removed. Removing one gives
 * its payments their own label back (rows re-filed by hand keep theirs) and refreshes the totals.
 */
export function PayeeRulesButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-role="payee-rules-open"
        className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--foreground-secondary)] transition-colors hover:bg-[var(--card-hover)] hover:text-[var(--foreground)]"
      >
        <ListChecks className="h-3.5 w-3.5" aria-hidden /> Payee rules
      </button>
      {open && <PayeeRulesDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function PayeeRulesDialog({ onClose }: { onClose: () => void }) {
  const { version, bump } = useDataVersion();
  const [rules, setRules] = useState<PayeeRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const single = useRef(createSingleFlight<{ relabelled: number }>());

  useEffect(() => {
    let cancelled = false;
    getJson<{ rules: PayeeRule[] }>("/api/payee-rules")
      .then((d) => {
        if (cancelled) return;
        setRules(d.rules || []);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, version]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && removing === null) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, removing]);

  const remove = (rule: PayeeRule) => {
    const p = single.current(() =>
      getJson<{ relabelled: number }>(`/api/payee-rules/${rule.id}`, { method: "DELETE" })
    );
    if (!p) return;
    setRemoving(rule.id);
    setRemoveError(null);
    p.then((res) => {
      setConfirming(null);
      // Ingest counts stored rows (an SMS and its e-mail copy are two), not payments.
      setNotice(
        `Removed the rule for ${rule.label || rule.key}; ${res.relabelled} stored row${
          res.relabelled === 1 ? "" : "s"
        } got their own label back (payments you re-filed by hand keep theirs).`
      );
      bump(); // refetches this list and every total on the page
    })
      .catch(() => setRemoveError("Couldn't remove the rule — nothing was changed. Try again."))
      .finally(() => setRemoving(null));
  };

  const dialog = (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center"
      onClick={(e) => {
        e.stopPropagation();
        if (removing === null) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="payee-rules-title"
        data-role="payee-rules"
        className="glass-card flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-b-none sm:mx-4 sm:rounded-b-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div>
            <h3
              id="payee-rules-title"
              className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase"
            >
              Payee rules
            </h3>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              Every payment to a payee below is filed under its category, past and future.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 transition-colors hover:bg-[var(--card-hover)]"
          >
            <X className="h-4 w-4 text-[var(--text-muted)]" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {notice && (
            <p className="mb-2 text-xs text-emerald-700 dark:text-emerald-400" aria-live="polite">
              {notice}
            </p>
          )}
          {removeError && (
            <p role="alert" className="mb-2 text-xs text-red-700 dark:text-red-400">
              {removeError}
            </p>
          )}
          {error ? (
            <ErrorState compact message={error} onRetry={() => setAttempt((a) => a + 1)} />
          ) : rules === null ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
            </div>
          ) : rules.length === 0 ? (
            <p className="py-8 text-center text-xs text-[var(--text-muted)]">
              No payee rules yet. Make one from any transaction&rsquo;s category button by ticking
              &ldquo;Also apply to all payments to …&rdquo;.
            </p>
          ) : (
            <ul className="divide-y divide-[var(--border)]/50">
              {rules.map((r) => (
                <li key={r.id} className="flex items-center gap-3 py-2.5" data-rule-id={r.id}>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-[var(--foreground)]">
                      {r.label || r.key}
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)]">
                      {ruleKeyLabel(r.key_type)} →{" "}
                      <span style={{ color: getCategoryColor(r.category) }}>{r.category}</span>
                      {typeof r.matching_transactions === "number" &&
                        ` · ${r.matching_transactions} payment${r.matching_transactions === 1 ? "" : "s"}`}
                      {r.created_at && ` · since ${r.created_at.slice(0, 10)}`}
                    </div>
                  </div>
                  {confirming === r.id ? (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => remove(r)}
                        disabled={removing !== null}
                        className="flex items-center gap-1 rounded-lg bg-red-600 px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-50"
                      >
                        {removing === r.id && <Loader2 className="h-3 w-3 animate-spin" />}
                        Remove
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        disabled={removing !== null}
                        className="rounded-lg px-2 py-1 text-[11px] text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)] disabled:opacity-50"
                      >
                        Keep
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setNotice(null);
                        setRemoveError(null);
                        setConfirming(r.id);
                      }}
                      aria-label={`Remove the rule for ${r.label || r.key}`}
                      className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--text-muted)] transition-colors hover:bg-[var(--card-hover)] hover:text-red-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
}
