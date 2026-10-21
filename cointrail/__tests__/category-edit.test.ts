import { describe, it, expect } from "vitest";
import {
  buildSaveRequest,
  canSave,
  createAfterSaveRefresh,
  createSingleFlight,
  groupChoices,
  outflowCards,
  outflowCategoryChoices,
  outflowGroupLabel,
  payeeOffer,
  ruleKeyLabel,
  saveSummary,
  versionedKey,
} from "@/lib/category-edit";

/**
 * Iteration 203 E3 / E9: the "change category" control on every transaction row and the payee
 * rules it can create. The page logic lives here so it is testable without a DOM.
 */

const CHOICES = [
  { name: "Food & Dining", group: "spend" },
  { name: "Transfer", group: "sent_to_people" },
  { name: "Household help", group: "spend" },
  { name: "Home Construction", group: "investments" },
  { name: "Self Transfer", group: "excluded" },
  { name: "Groceries", group: "spend" },
  { name: "Loan Repayment", group: "loans" },
  { name: "Local transport", group: "spend" },
  { name: "Outings & misc", group: "spend" },
];
const OWNER = ["Household help", "Local transport", "Outings & misc"];

describe("groupChoices (E3 AC2)", () => {
  it("orders groups Spending → Sent to people → Investments → Loans → Not counted", () => {
    const groups = groupChoices(CHOICES, OWNER);
    expect(groups.map((g) => g.group)).toEqual([
      "spend",
      "sent_to_people",
      "investments",
      "loans",
      "excluded",
    ]);
    expect(groups[0].label).toBe("Spending");
    expect(groups[1].label).toBe("Sent to people");
  });

  it("puts the owner's new categories first inside Spending, the rest alphabetically", () => {
    const spend = groupChoices(CHOICES, OWNER)[0];
    expect(spend.names).toEqual([
      "Household help",
      "Local transport",
      "Outings & misc",
      "Food & Dining",
      "Groceries",
    ]);
  });

  it("filters by a case-insensitive search and drops empty groups", () => {
    const groups = groupChoices(CHOICES, OWNER, "TRANS");
    expect(groups.map((g) => g.group)).toEqual(["spend", "sent_to_people", "excluded"]);
    expect(groups[0].names).toEqual(["Local transport"]);
  });

  it("keeps a category whose group Ingest does not know, in a trailing group", () => {
    const groups = groupChoices([...CHOICES, { name: "Weird", group: "mystery" }], OWNER);
    expect(groups[groups.length - 1].names).toEqual(["Weird"]);
  });
});

describe("payeeOffer (E3 AC3)", () => {
  it("offers 'apply to all payments to <payee>' with the count when Ingest names a payee", () => {
    const offer = payeeOffer({
      transaction_id: 5,
      payee: { key_type: "vpa", key: "maid77@okaxis", label: "maid77@okaxis" },
      refused: null,
      rule: null,
      matching_transactions: 7,
    });
    expect(offer).toMatchObject({ canApply: true, label: "maid77@okaxis", count: 7 });
    expect(offer.keyTypeLabel).toBe("UPI ID");
    expect(offer.existingRule).toBeNull();
  });

  it("explains why no rule is possible for generic text", () => {
    const offer = payeeOffer({
      transaction_id: 6,
      payee: null,
      refused: "'DEBIT' names no specific payee",
      rule: null,
      matching_transactions: 0,
    });
    expect(offer.canApply).toBe(false);
    expect(offer.refused).toBe("'DEBIT' names no specific payee");
  });

  it("shows an existing rule for the payee", () => {
    const offer = payeeOffer({
      transaction_id: 7,
      payee: { key_type: "account", key: "9999", label: "a/c ...9999" },
      refused: null,
      rule: {
        id: 1,
        key_type: "account",
        key: "9999",
        label: "a/c ...9999",
        category: "Home Construction",
        matching_transactions: 6,
      },
      matching_transactions: 6,
    });
    expect(offer.existingRule?.category).toBe("Home Construction");
    expect(offer.keyTypeLabel).toBe("account");
  });

  it("is a no-op while the lookup has not answered", () => {
    expect(payeeOffer(null)).toMatchObject({ canApply: false, label: null, count: 0 });
  });
});

describe("saveSummary (E3 AC3 / E9 AC2: show what the save did)", () => {
  const base = {
    status: "updated",
    transaction: { id: 9, category: "Household help", merchant: "Maid" },
    rule: null,
    rule_refused: null,
    relabelled: 0,
    relabelled_ids: [],
  };

  it("one row only", () => {
    expect(saveSummary(base, false)).toEqual({
      headline: "Filed under Household help.",
      rule: null,
      refused: null,
    });
  });

  it("names the rule it created, the payments it covers and the stored rows it relabelled", () => {
    const s = saveSummary(
      {
        ...base,
        rule: {
          id: 3,
          key_type: "vpa",
          key: "maid77@okaxis",
          label: "maid77@okaxis",
          category: "Household help",
          matching_transactions: 8,
        },
        relabelled: 7,
        relabelled_ids: [1, 2, 3, 4, 5, 6, 7],
      },
      true
    );
    expect(s.rule).toBe(
      "Rule saved: every payment to maid77@okaxis (UPI ID) goes to Household help. It covers 8 payments; 7 other stored rows were relabelled (an SMS and its e-mail copy count as two)."
    );
    expect(s.refused).toBeNull();
  });

  it("does not call stored copies 'payments' (Ingest's relabelled count includes merged copies)", () => {
    const s = saveSummary(
      {
        ...base,
        rule: {
          id: 2,
          key_type: "name",
          key: "lakshmi bai",
          label: "Lakshmi Bai",
          category: "Transfer",
          matching_transactions: 2,
        },
        relabelled: 3,
        relabelled_ids: [101, 102, 103],
      },
      true
    );
    expect(s.rule).toContain("It covers 2 payments; 3 other stored rows were relabelled");
  });

  it("says when the rule was asked for but refused", () => {
    const s = saveSummary({ ...base, rule_refused: "'DEBIT' names no specific payee" }, true);
    expect(s.rule).toBeNull();
    expect(s.refused).toBe("No payee rule made: 'DEBIT' names no specific payee.");
  });

  it("singular wording, none relabelled, and a rule without a count", () => {
    const rule = {
      id: 3,
      key_type: "name",
      key: "snabbit",
      label: "Snabbit",
      category: "Household help",
      matching_transactions: 1,
    };
    expect(saveSummary({ ...base, rule, relabelled: 1 }, true).rule).toContain(
      "It covers 1 payment; 1 other stored row was relabelled"
    );
    expect(saveSummary({ ...base, rule, relabelled: 0 }, true).rule).toContain(
      "no other stored rows needed relabelling"
    );
    const noCount = { ...rule, matching_transactions: undefined };
    expect(saveSummary({ ...base, rule: noCount, relabelled: 0 }, true).rule).toBe(
      "Rule saved: every payment to Snabbit (payee name) goes to Household help. No other stored rows needed relabelling."
    );
  });
});

describe("outflow categories (E3: payments to people must be reachable to re-file them)", () => {
  it("names the categories counted beside Spending, grouped", () => {
    expect(outflowCategoryChoices(CHOICES)).toEqual([
      { name: "Transfer", group: "sent_to_people" },
      { name: "Home Construction", group: "investments" },
      { name: "Loan Repayment", group: "loans" },
    ]);
  });

  it("builds cards from the category drills, biggest first, empty ones dropped", () => {
    const cards = outflowCards(
      [
        { name: "Transfer", group: "sent_to_people" },
        { name: "Family Remittance", group: "sent_to_people" },
        { name: "Loan Repayment", group: "loans" },
      ],
      [
        {
          category: "Transfer",
          total: 4000,
          merchants: [
            { name: "Lakshmi Bai", total: 3000, count: 2 },
            { name: "Ravi", total: 1000, count: 1 },
          ],
        },
        {
          category: "Family Remittance",
          total: 50000,
          merchants: [{ name: "Nani", total: 50000, count: 3 }],
        },
        { category: "Loan Repayment", total: 0, merchants: [] },
      ]
    );
    expect(cards.map((c) => [c.category, c.group, c.total, c.count])).toEqual([
      ["Family Remittance", "sent_to_people", 50000, 3],
      ["Transfer", "sent_to_people", 4000, 3],
    ]);
  });

  it("labels each outflow group", () => {
    expect(outflowGroupLabel("sent_to_people")).toBe("Sent to people");
    expect(outflowGroupLabel("loans")).toBe("Loan repayments");
  });
});

describe("canSave", () => {
  it("needs a pick that differs, unless the payee rule is asked for", () => {
    expect(canSave("Transfer", null, false)).toBe(false);
    expect(canSave("Transfer", "Transfer", false)).toBe(false);
    expect(canSave("Transfer", "Household help", false)).toBe(true);
    expect(canSave("Household help", "Household help", true)).toBe(true);
  });
});

describe("ruleKeyLabel", () => {
  it("words each key type", () => {
    expect(ruleKeyLabel("vpa")).toBe("UPI ID");
    expect(ruleKeyLabel("account")).toBe("account");
    expect(ruleKeyLabel("name")).toBe("payee name");
    expect(ruleKeyLabel("other")).toBe("other");
  });
});

describe("createSingleFlight (E3: double-submit sends one request)", () => {
  it("ignores a second call while the first is in flight, then allows a new one", async () => {
    const run = createSingleFlight<number>();
    let calls = 0;
    let release!: (v: number) => void;
    const first = run(
      () =>
        new Promise<number>((r) => {
          calls++;
          release = r;
        })
    );
    const second = run(async () => {
      calls++;
      return 2;
    });
    expect(second).toBeNull();
    release(1);
    await expect(first).resolves.toBe(1);
    const third = run(async () => {
      calls++;
      return 3;
    });
    await expect(third).resolves.toBe(3);
    expect(calls).toBe(2);
  });

  it("frees the slot when the request fails", async () => {
    const run = createSingleFlight<number>();
    await expect(run(() => Promise.reject(new Error("boom")))).rejects.toThrow("boom");
    await expect(run(async () => 4)).resolves.toBe(4);
  });
});

describe("buildSaveRequest (E3 AC4: the control sends {category, apply_to_payee})", () => {
  it("PUTs the picked category and whether the payee rule was asked for", () => {
    const { url, init } = buildSaveRequest(42, "Household help", true);
    expect(url).toBe("/api/transactions/42");
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      category: "Household help",
      apply_to_payee: true,
    });
  });

  it("sends apply_to_payee false for a one-row change", () => {
    expect(JSON.parse(buildSaveRequest(7, "Outings & misc", false).init.body as string)).toEqual({
      category: "Outings & misc",
      apply_to_payee: false,
    });
  });
});

describe("createAfterSaveRefresh (E3 AC3 amended: totals refresh when the chooser closes after a save)", () => {
  it("refreshes the page once when the chooser closes after a save", () => {
    const refresh = createAfterSaveRefresh();
    let bumps = 0;
    let changed = 0;
    refresh.saved();
    expect(
      refresh.close(
        () => bumps++,
        () => changed++
      )
    ).toBe(true);
    expect([bumps, changed]).toEqual([1, 1]);
    // closing again (or a second close event) does not refresh twice
    expect(refresh.close(() => bumps++)).toBe(false);
    expect(bumps).toBe(1);
  });

  it("does not refresh when the chooser closes without a save (cancel, Escape, a failed save)", () => {
    const refresh = createAfterSaveRefresh();
    let bumps = 0;
    expect(refresh.close(() => bumps++)).toBe(false);
    expect(bumps).toBe(0);
  });
});

describe("versionedKey (E3: a bumped data version refetches every keyed block)", () => {
  it("changes when the data version moves, and only then", () => {
    const base = "/api/expenses/dashboard/categories?from=2026-08-01&to=2026-10-20#0";
    expect(versionedKey(base, 0)).not.toBe(versionedKey(base, 1));
    expect(versionedKey(base, 3)).toBe(versionedKey(base, 3));
    expect(versionedKey(base, 2).startsWith(base)).toBe(true);
  });
});
