/**
 * Setting a transaction's category from the site (iteration 203 E3) and the payee rules it can
 * create (E9). The control and the rules panel render these; the logic lives here so it is tested
 * without a DOM.
 *
 * A save re-files ONE row. Only when the owner ticks "Also apply to all payments to <payee>" does
 * Ingest create a rule, keyed on the row's UPI ID, counterparty account or exact payee name; generic
 * text ("DEBIT") names no payee and Ingest says so (`rule_refused`). Nothing is learned silently.
 */

export type CategoryGroupId = "spend" | "sent_to_people" | "investments" | "loans" | "excluded";

export interface CategoryChoice {
  name: string;
  /** Where the category counts on the Expenses page (Ingest's analytics.category_group). */
  group: string;
}

export interface ChoiceGroup {
  group: string;
  label: string;
  note: string;
  names: string[];
}

const GROUP_ORDER: CategoryGroupId[] = [
  "spend",
  "sent_to_people",
  "investments",
  "loans",
  "excluded",
];

const GROUP_TEXT: Record<string, { label: string; note: string }> = {
  spend: { label: "Spending", note: "counted in Spending" },
  sent_to_people: { label: "Sent to people", note: "shown beside Spending, not in it" },
  investments: { label: "Investments", note: "shown beside Spending, not in it" },
  loans: { label: "Loan repayments", note: "shown beside Spending, not in it" },
  excluded: { label: "Not counted", note: "moves between your own accounts, card bills" },
};

/**
 * The category chooser's sections, in the order the Expenses page shows money: Spending first
 * (the owner's own categories — Household help, Local transport, Outings & misc — at its top),
 * then Sent to people, Investments, Loans, Not counted. `query` filters by name; empty sections are
 * dropped; a group Ingest names that this list does not know comes last rather than vanishing.
 */
export function groupChoices(
  choices: CategoryChoice[],
  ownerCategories: string[] = [],
  query = ""
): ChoiceGroup[] {
  const q = query.trim().toLowerCase();
  const picked = q ? choices.filter((c) => c.name.toLowerCase().includes(q)) : choices;
  const known = new Set<string>(GROUP_ORDER);
  const extraGroups = Array.from(
    new Set(picked.map((c) => c.group).filter((g) => !known.has(g)))
  ).sort();
  const ownerRank = (name: string) => {
    const i = ownerCategories.indexOf(name);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  return [...GROUP_ORDER, ...extraGroups]
    .map((group) => {
      const names = picked
        .filter((c) => c.group === group)
        .map((c) => c.name)
        .sort((a, b) => ownerRank(a) - ownerRank(b) || a.localeCompare(b));
      const text = GROUP_TEXT[group] ?? { label: "Other", note: "" };
      return { group, label: text.label, note: text.note, names };
    })
    .filter((g) => g.names.length > 0);
}

/*
 * Outside Spending (E3). Payments to people (a maid, a driver, a friend for dinner) are counted in
 * "Sent to people", beside Spending — so the Categories tab, which lists spending only, never
 * showed them and they could not be re-filed. These helpers name the categories of the outflow
 * groups and build their cards from the same category drill the spending cards use.
 */

const OUTFLOW_GROUPS = ["sent_to_people", "investments", "loans"];

/** The categories counted beside Spending (Sent to people, Investments, Loan repayments). */
export function outflowCategoryChoices(choices: CategoryChoice[]): CategoryChoice[] {
  return choices.filter((c) => OUTFLOW_GROUPS.includes(c.group));
}

/** "Sent to people", "Investments" or "Loan repayments". */
export function outflowGroupLabel(group: string): string {
  return GROUP_TEXT[group]?.label ?? group;
}

export interface OutflowCard {
  category: string;
  group: string;
  total: number;
  count: number;
  merchants: { name: string; total: number; count: number }[];
}

/** One card per outflow category that has money in the window, biggest first. */
export function outflowCards(
  choices: CategoryChoice[],
  drills: {
    category: string;
    total: number;
    merchants: { name: string; total: number; count: number }[];
  }[]
): OutflowCard[] {
  return drills
    .map((d, i) => ({
      category: d.category,
      group: choices[i]?.group ?? "",
      total: d.total,
      count: d.merchants.reduce((s, m) => s + m.count, 0),
      merchants: d.merchants.map((m) => ({ name: m.name, total: m.total, count: m.count })),
    }))
    .filter((c) => c.total !== 0)
    .sort((a, b) => b.total - a.total);
}

export interface PayeeRule {
  id: number;
  key_type: string;
  key: string;
  label: string | null;
  category: string;
  merchant_name?: string | null;
  source?: string | null;
  created_at?: string | null;
  matching_transactions?: number;
}

/** Ingest GET /transactions/{id}/payee. */
export interface PayeeInfo {
  transaction_id: number;
  payee: { key_type: string; key: string; label: string | null } | null;
  refused: string | null;
  rule: PayeeRule | null;
  matching_transactions: number;
}

export interface PayeeOffer {
  canApply: boolean;
  label: string | null;
  keyTypeLabel: string | null;
  /** Live rows of this payee (the one being edited included). */
  count: number;
  existingRule: PayeeRule | null;
  refused: string | null;
}

const KEY_TYPE_LABELS: Record<string, string> = {
  vpa: "UPI ID",
  account: "account",
  name: "payee name",
};

/** "UPI ID", "account" or "payee name" — what a rule is keyed on. */
export function ruleKeyLabel(keyType: string): string {
  return KEY_TYPE_LABELS[keyType] ?? keyType;
}

/** Whether the chooser can offer "Also apply to all payments to <payee>", and the words for it. */
export function payeeOffer(info: PayeeInfo | null): PayeeOffer {
  if (!info) {
    return {
      canApply: false,
      label: null,
      keyTypeLabel: null,
      count: 0,
      existingRule: null,
      refused: null,
    };
  }
  const payee = info.payee;
  return {
    canApply: !!payee,
    label: payee ? payee.label || payee.key : null,
    keyTypeLabel: payee ? ruleKeyLabel(payee.key_type) : null,
    count: info.matching_transactions || 0,
    existingRule: info.rule,
    refused: payee ? null : info.refused,
  };
}

/** Ingest PUT /transactions/{id}. */
export interface SaveResponse {
  status: string;
  transaction: { id: number; category: string | null; merchant?: string | null };
  rule: PayeeRule | null;
  rule_refused: string | null;
  relabelled: number;
  relabelled_ids: number[];
}

export interface SaveSummary {
  headline: string;
  /** The rule the save created, in words (E9 AC2: show the learned rule). */
  rule: string | null;
  /** The owner asked for a rule and Ingest made none. */
  refused: string | null;
}

/** What the save did, for the confirmation the chooser shows. */
export function saveSummary(res: SaveResponse, askedForRule: boolean): SaveSummary {
  const headline = `Filed under ${res.transaction.category}.`;
  if (res.rule) {
    // Ingest's `relabelled` counts stored ROWS: a payment merged from an SMS and its e-mail copy is
    // two of them. The payments a rule covers are its `matching_transactions` (live rows).
    const others = res.relabelled;
    const rows =
      others === 0
        ? "no other stored rows needed relabelling"
        : `${others} other stored row${others === 1 ? " was" : "s were"} relabelled (an SMS and its e-mail copy count as two)`;
    const covers = res.rule.matching_transactions;
    const who = res.rule.label || res.rule.key;
    const head = `Rule saved: every payment to ${who} (${ruleKeyLabel(res.rule.key_type)}) goes to ${res.rule.category}.`;
    return {
      headline,
      rule:
        typeof covers === "number"
          ? `${head} It covers ${covers} payment${covers === 1 ? "" : "s"}; ${rows}.`
          : `${head} ${rows.charAt(0).toUpperCase()}${rows.slice(1)}.`,
      refused: null,
    };
  }
  if (askedForRule) {
    return {
      headline,
      rule: null,
      refused: res.rule_refused
        ? `No payee rule made: ${res.rule_refused}.`
        : "No payee rule made.",
    };
  }
  return { headline, rule: null, refused: null };
}

/** The request a save sends: Ingest PUT /transactions/{id} through the Next proxy (E3 AC4). */
export function buildSaveRequest(
  txnId: number,
  category: string,
  applyToPayee: boolean
): { url: string; init: RequestInit } {
  return {
    url: `/api/transactions/${txnId}`,
    init: {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category, apply_to_payee: applyToPayee }),
    },
  };
}

/**
 * The page refreshes when the chooser closes after a save (E3 AC3 as amended), not while it is
 * open: a re-filed row usually leaves the list it was opened from, and refreshing first would
 * unmount the row — and with it the confirmation that names the rule the save created.
 * `saved()` records a successful save; `close(refresh, onChanged)` runs them once if there was one.
 */
export function createAfterSaveRefresh() {
  let pending = false;
  return {
    saved() {
      pending = true;
    },
    close(refresh: () => void, onChanged?: () => void): boolean {
      if (!pending) return false;
      pending = false;
      onChanged?.();
      refresh();
      return true;
    },
  };
}

/** A fetch key that moves with the page's data version, so a bump refetches every keyed block (E3). */
export function versionedKey(base: string, version: number): string {
  return `${base}#v${version}`;
}

/** A save needs a chosen category that differs from the current one, or the payee rule ticked. */
export function canSave(current: string | null, picked: string | null, applyToPayee: boolean) {
  if (!picked) return false;
  return picked !== current || applyToPayee;
}

/**
 * One request at a time: `run(fn)` starts `fn` unless one is already in flight, in which case it
 * returns null and sends nothing (a double-click on Save or Undo is one change, not two).
 */
export function createSingleFlight<T>() {
  let inFlight = false;
  return (fn: () => Promise<T>): Promise<T> | null => {
    if (inFlight) return null;
    inFlight = true;
    let p: Promise<T>;
    try {
      p = fn();
    } catch (e) {
      inFlight = false;
      return Promise.reject(e);
    }
    return p.finally(() => {
      inFlight = false;
    });
  };
}
