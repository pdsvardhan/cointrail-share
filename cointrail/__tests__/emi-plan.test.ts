import { describe, it, expect } from "vitest";
import { emiPlanView, isEmiPlanItem } from "@/lib/emi";

/**
 * Iteration 203 E6: an EMI plan is ONE item — the purchase, the merchant, the tenure, the monthly
 * payment (principal + interest + GST), x of n paid, the end date and the interest paid so far —
 * instead of an "INTEREST ON EMI ₹291.89/mo" row that showed only the interest.
 */

const plan = {
  plan_id: "0077:2026-08:6:fp",
  series_key: "plan:0077:2026-08:6:fp",
  bank: "SBI",
  card_last4: "0077",
  merchant: "AcmeShop",
  purchase_amount: 12000,
  purchase_date: "2026-07-12",
  tenure_months: 6,
  interest_rate_pct: 15,
  processing_fee: 45,
  emi_amount_bank: 2088.4,
  first_emi_date: "2026-08-05",
  end_date: "2027-01-05",
  next_due: "2026-11-05",
  installments_billed: 2,
  installments_paid: 3,
  installments_remaining: 3,
  monthly_payment: 2121.98,
  interest_paid: 300.1,
  tax_paid: 54.02,
  interest_and_tax_paid: 354.12,
  principal_remaining: 6100.5,
  estimated_installments: 1,
  figures_installments: 3,
  status: "active",
};

describe("emiPlanView", () => {
  it("names the purchase, not the interest line", () => {
    const v = emiPlanView(plan);
    expect(v.title).toBe("AcmeShop");
    expect(v.purchase).toBe("₹12,000.00 bought 12 Jul 2026");
    expect(v.tenure).toBe("6 months at 15% p.a.");
  });

  it("shows the full monthly payment and the bank's EMI, never the interest alone", () => {
    const v = emiPlanView(plan);
    expect(v.monthly).toBe(2121.98);
    expect(v.bankEmi).toBe(2088.4);
    expect(v.monthly).toBeGreaterThan(plan.interest_paid);
  });

  it("progress, end date, next due and interest + GST paid so far", () => {
    const v = emiPlanView(plan);
    expect(v.progressText).toBe("3 of 6 paid");
    expect(v.progressPct).toBe(50);
    expect(v.endLabel).toBe("Ends 5 Jan 2027");
    expect(v.nextDue).toBe("5 Nov 2026");
    expect(v.interestPaid).toBe(354.12);
    expect(v.statusLabel).toBe("Active");
    expect(v.card).toBe("SBI ··0077");
  });

  it("copes with a plan whose booking details are unknown", () => {
    const v = emiPlanView({
      ...plan,
      purchase_amount: null,
      purchase_date: null,
      tenure_months: null,
      interest_rate_pct: null,
      end_date: null,
      next_due: null,
      installments_paid: 4,
      emi_amount_bank: null,
      status: "ended",
    });
    expect(v.purchase).toBeNull();
    expect(v.tenure).toBeNull();
    expect(v.progressText).toBe("4 paid");
    expect(v.progressPct).toBeNull();
    expect(v.endLabel).toBeNull();
    expect(v.bankEmi).toBeNull();
    expect(v.statusLabel).toBe("Ended");
  });

  it("caps progress at 100% and words a completed plan", () => {
    const v = emiPlanView({ ...plan, installments_paid: 7, status: "completed" });
    expect(v.progressPct).toBe(100);
    expect(v.statusLabel).toBe("Completed");
  });
});

describe("figuresNote (E6 round 2: the money figures cover the same instalments as 'x of n paid')", () => {
  it("says when an instalment the bank has charged is estimated (not on a statement yet)", () => {
    expect(emiPlanView(plan).figuresNote).toBe(
      "Includes 1 instalment the bank has charged that no statement shows yet (estimated)."
    );
    expect(emiPlanView({ ...plan, estimated_installments: 2 }).figuresNote).toBe(
      "Includes 2 instalments the bank has charged that no statement shows yet (estimated)."
    );
  });

  it("says how far the figures go when they could not be estimated", () => {
    const v = emiPlanView({ ...plan, estimated_installments: 0, figures_installments: 2 });
    expect(v.figuresNote).toBe("Interest and principal cover the 2 instalments on statements.");
  });

  it("says nothing when the figures cover every paid instalment", () => {
    expect(
      emiPlanView({ ...plan, estimated_installments: 0, figures_installments: 3 }).figuresNote
    ).toBeNull();
    expect(
      emiPlanView({ ...plan, estimated_installments: undefined, figures_installments: undefined })
        .figuresNote
    ).toBeNull();
  });
});

describe("isEmiPlanItem", () => {
  it("is true only for a recurring row that carries an emi_plan", () => {
    expect(isEmiPlanItem({ type: "emi", emi_plan: plan })).toBe(true);
    expect(isEmiPlanItem({ type: "emi" })).toBe(false);
    expect(isEmiPlanItem({ type: "subscription", emi_plan: null })).toBe(false);
  });
});
