"""iter-203 E6 — an EMI plan is one item, not a stray "INTEREST ON EMI" subscription.

The wordings are the banks' own (SBI Merchant EMI booking e-mail + SMS + statement lines "FP EMI 01/06
(EXCL TAX 93.49)" / "INTEREST ON EMI" / "MERCHANT EMI PROC FEE"; ICICI "Principal/Interest Amount
Amortization - <x/n>MERCHANT" with the marker sometimes missing; HDFC "OFFUS EMI,PRIN NB:x,<loan>").
Card numbers, merchants and amounts are made up; the SBI plan reproduces the shape of the real one
(6 months at 16.5%, a first instalment carrying broken-period interest)."""
from datetime import date

from modules.txn_store import derive_kind
from routes.transactions import detect_recurring_endpoint, list_emi_plans
from services.emi_plans import detect_emi_plans

TODAY = date(2026, 10, 20)

SBI_BOOKING_EMAIL = (
    "S2S Communication Dear CARDHOLDER, Thank you for booking the SBI Card Merchant EMI facility. "
    "We value your relationship with SBI Card. For your future reference, we are detailing your booking "
    "details below: Transaction Amount: Rs.&nbsp;12,000.00 Transaction Date(DD/MM/YYYY)): 27/07/2026 "
    "Booking Amount: Rs.&nbsp;12,000.00 Rate of Interest: 16.50&nbsp;% p.a. Tenure: 06&nbsp;months "
    "Processing Fee (Applicable Taxes): Rs.&nbsp;45.00 EMI Amount: Rs.&nbsp;2,163.45 "
    "First EMI Date(DD/MM/YYYY): 19/08/2026 We've enclosed your amortization schedule.")
SBI_BOOKING_SMS = ("Dear SBI Credit Cardholder, Your EMI Booking amount of Rs.12000 at AcmeShopInterne has been "
                   "converted to 6 EMIs at 16.5 percent reducing rate of interest and Processing Fee of Rs.45 on "
                   "29/07/2026. TnC apply.")
ICICI_SMS = ("Dear Customer, your transaction of Rs 9,000.00 using ICICI Bank Credit Card XX0099 has been "
             "converted into EMI on 13-04-26.")


def txn(conn, card, d, amount, desc, kind=None, source="card", txn_type="debit", category="EMI", merchant=None):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, txn_type, "
        "confidence, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'high', ?)",
        (amount, desc, merchant or desc, category, d, source, card, txn_type,
         kind or derive_kind(txn_type, category, desc, "card")),
    )
    conn.commit()
    return cur.lastrowid


def setup_sbi(conn, make_card):
    make_card("SBI", "0077", name="Cashback")
    # kinds as the statement parser stores them for these SBI lines
    purchase = txn(conn, "0077", "2026-07-27", 12000.0, "#AcmeShop Internet P Bangalore IN (Pay in EMIs)",
                   category="Shopping", merchant="AcmeShop", kind="purchase")
    fee = txn(conn, "0077", "2026-07-29", 45.0, "MERCHANT EMI PROC FEE (EXCL TAX 8.10)", kind="fee")
    i1 = txn(conn, "0077", "2026-08-19", 231.0, "INTEREST ON EMI", kind="interest")
    e1 = txn(conn, "0077", "2026-08-19", 2163.45, "FP EMI 01/06(EXCL TAX 41.58)", kind="emi_principal")
    i2 = txn(conn, "0077", "2026-09-19", 136.4, "INTEREST ON EMI", kind="interest")
    e2 = txn(conn, "0077", "2026-09-19", 2097.43, "FP EMI 02/06(EXCL TAX 24.55)", kind="emi_principal")
    conn.execute("INSERT INTO emails (gmail_id, sender, sender_email, subject, date, body_text, is_financial) "
                 "VALUES ('g-emi-1', 'SBI Card', 'booking.confirmation@sbicard.com', "
                 "'Important! Details of Merchant EMI booked on your SBI Credit Card', "
                 "'Thu, 30 Jul 2026 10:50:42 +0000', ?, 1)", (SBI_BOOKING_EMAIL,))
    conn.execute("INSERT INTO sms (sender, body, phone_timestamp, is_financial) VALUES ('JM-SBICRD-S', ?, "
                 "'2026-07-31T12:33:09', 1)", (SBI_BOOKING_SMS,))
    conn.commit()
    return {"purchase": purchase, "fee": fee, "lines": {i1, e1, i2, e2}}


def test_the_sbi_merchant_emi_is_one_plan_with_the_banks_figures(conn, make_card):
    ids = setup_sbi(conn, make_card)
    (plan,) = detect_emi_plans(conn, today=TODAY)
    assert plan["card_last4"] == "0077" and plan["bank"] == "SBI"
    assert plan["merchant"] == "AcmeShop" and plan["purchase_txn_id"] == ids["purchase"]
    assert plan["purchase_amount"] == 12000.0 and plan["purchase_date"] == "2026-07-27"
    assert plan["tenure_months"] == 6 and plan["interest_rate_pct"] == 16.5
    assert plan["emi_amount_bank"] == 2163.45 and plan["processing_fee"] == 45.0
    assert plan["first_emi_date"] == "2026-08-19" and plan["end_date"] == "2027-01-19"
    # two instalments are on statements; the bank's schedule has charged the third on 19 Oct
    assert plan["installments_billed"] == 2 and plan["installments_paid"] == 3
    assert plan["next_due"] == "2026-11-19" and plan["status"] == "active"
    first, second = plan["installments"][:2]
    assert (first["n"], first["amount"], first["interest"], first["tax"]) == (1, 2163.45, 231.0, 41.58)
    assert first["principal"] == round(2163.45 - 231.0, 2)
    billed = [i for i in plan["installments"] if not i.get("estimated")]
    assert round(sum(i["interest"] for i in billed), 2) == round(231.0 + 136.4, 2)
    assert round(sum(i["tax"] for i in billed), 2) == round(41.58 + 24.55, 2)
    # every figure covers the 3 instalments the schedule has charged; the 3rd is estimated
    assert plan["estimated_installments"] == 1 and plan["figures_installments"] == 3
    assert plan["monthly_payment"] == round(2097.43 + 24.55, 2)     # latest instalment incl. GST
    third = plan["installments"][-1]
    assert third["n"] == 3 and third["estimated"] is True and third["date"] == "2026-10-19"
    assert plan["principal_remaining"] == round(
        12000.0 - (2163.45 - 231.0) - (2097.43 - 136.4) - third["principal"], 2)
    assert set(plan["txn_ids"]) == ids["lines"] | {ids["fee"]}
    assert "booking_email" in plan["sources"] and "booking_sms" in plan["sources"]


def test_recurring_shows_the_plan_once_and_no_interest_only_row(conn, make_card):
    setup_sbi(conn, make_card)
    emis = [r for r in detect_recurring_endpoint()["recurring"] if r["type"] == "emi"]
    assert len(emis) == 1
    (row,) = emis
    assert row["series_key"].startswith("plan:") and row["display_name"] == "AcmeShop (EMI)"
    assert row["total_installments"] == 6 and row["emi_plan"]["merchant"] == "AcmeShop"
    assert not any("INTEREST ON EMI" in (r["merchant"] or "") for r in detect_recurring_endpoint(include_ended=True)["recurring"])
    assert list_emi_plans(card_last4="0077")["plans"][0]["plan_id"] == row["emi_plan"]["plan_id"]
    assert list_emi_plans(card_last4="9999")["plans"] == []


def test_icici_amortisation_lines_with_and_without_markers_form_one_plan(conn, make_card):
    make_card("ICICI", "0099", name="Amazon Pay")
    txn(conn, "0099", "2026-04-13", 9000.0, "AMAZON PAY INDIA PVT LT", category="Shopping", kind="purchase")
    conn.execute("INSERT INTO sms (sender, body, phone_timestamp, is_financial) VALUES ('AX-ICICIT-S', ?, "
                 "'2026-04-14T05:39:42', 1)", (ICICI_SMS,))
    months = ["2026-04-15", "2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"]
    for x, d in enumerate(months, start=1):
        marked = x in (1, 2, 5, 6)
        tail = f"<{x}/6>AMAZON PAY INDIA PVT LT 0" if marked else "0"
        txn(conn, "0099", d, 1450.0 + 10 * x, f"Principal Amount Amortization - {tail}", kind="emi_principal")
        txn(conn, "0099", d, 120.0 - 15 * x, f"Interest Amount Amortization - {tail}", kind="interest")
    (plan,) = detect_emi_plans(conn, today=TODAY)
    assert plan["bank"] == "ICICI" and plan["tenure_months"] == 6
    assert plan["installments_billed"] == 6 and plan["installments_paid"] == 6 and plan["status"] == "completed"
    assert plan["purchase_amount"] == 9000.0 and plan["merchant"] == "AMAZON PAY INDIA PVT LT"
    assert [i["n"] for i in plan["installments"]] == [1, 2, 3, 4, 5, 6]
    assert plan["next_due"] is None and plan["end_date"] == "2026-09-15"
    assert detect_recurring_endpoint()["recurring"] == []           # a finished plan is not active
    assert len(detect_recurring_endpoint(include_ended=True)["recurring"]) == 1


def test_hdfc_offus_emi_interest_is_interest_not_principal(conn, make_card):
    assert derive_kind("debit", "EMI", "OFFUS EMI,INT NBR:07,00000100000001 (Ref# 0999)", "card") == "interest"
    assert derive_kind("debit", "EMI", "OFFUS EMI,PRIN NB:10,00000100000002 (Ref# 0999)", "card") == "emi_principal"
    make_card("HDFC", "0055", name="Millennia")
    for x, d in ((7, "2026-07-20"), (8, "2026-08-20"), (9, "2026-09-20")):
        txn(conn, "0055", d, 900.0, f"OFFUS EMI,PRIN NB:{x:02d},00000100000001 (Ref# 09{x})")
        txn(conn, "0055", d, 80.0 - x, f"OFFUS EMI,INT NBR:{x:02d},00000100000001 (Ref# 09{x}1)")
    (plan,) = detect_emi_plans(conn, today=TODAY)
    assert plan["bank"] == "HDFC" and plan["tenure_months"] is None
    assert [i["n"] for i in plan["installments"]] == [7, 8, 9]
    assert plan["interest_paid"] == round(73.0 + 72.0 + 71.0, 2) and plan["status"] == "active"


def test_the_data_step_rekinds_stored_hdfc_interest_lines(conn):
    from scripts.iter203_be2_steps import step_hdfc_emi_interest_kind
    old = txn(conn, "0055", "2025-12-20", 75.0, "OFFUS EMI,INT NBR:10,00000100000001 (Ref# 0910)", kind="emi_principal")
    prin = txn(conn, "0055", "2025-12-20", 900.0, "OFFUS EMI,PRIN NB:10,00000100000001 (Ref# 0911)", kind="emi_principal")
    dry = step_hdfc_emi_interest_kind(conn, dry_run=True)
    assert dry["ids"] == [old] and dry["live_amount"] == 75.0
    assert conn.execute("SELECT kind FROM transactions WHERE id = ?", (old,)).fetchone()[0] == "emi_principal"
    step_hdfc_emi_interest_kind(conn, dry_run=False)
    kinds = dict(conn.execute("SELECT id, kind FROM transactions").fetchall())
    assert kinds[old] == "interest" and kinds[prin] == "emi_principal"
    assert step_hdfc_emi_interest_kind(conn, dry_run=True)["rows"] == 0


def test_an_old_booking_without_its_early_statements_is_not_an_active_empty_plan(conn, make_card):
    """ICICI booked a conversion in Feb 2025; statement lines exist only from Aug 2025, unmarked."""
    make_card("ICICI", "0088", name="Sapphiro")
    txn(conn, "0088", "2025-02-10", 30000.0, "ACME ELECTRONICS", category="Shopping", kind="purchase",
        merchant="Acme Electronics")
    conn.execute("INSERT INTO sms (sender, body, phone_timestamp, is_financial) VALUES ('JX-ICICIT', ?, "
                 "'2025-02-11T05:41:44', 1)",
                 ("Dear Customer, your transaction of Rs 30,000.00 using ICICI Bank Credit Card XX0088 has been "
                  "converted into EMI on 10-02-25.",))
    conn.commit()
    for x, d in enumerate(["2025-08-10", "2025-09-10", "2025-10-10", "2025-11-10", "2025-12-10", "2026-01-10"]):
        txn(conn, "0088", d, 2400.0 + x, "Principal Amount Amortization - 0", kind="emi_principal")
        txn(conn, "0088", d, 90.0 - x, "Interest Amount Amortization - 0", kind="interest")
    plans = detect_emi_plans(conn, today=TODAY)
    assert len(plans) == 1
    (plan,) = plans
    assert plan["merchant"] == "Acme Electronics" and plan["purchase_amount"] == 30000.0
    assert plan["status"] == "ended" and plan["next_due"] is None
    # the statements do not cover the first months, so what is left cannot be computed
    assert plan["principal_remaining"] is None and plan["statement_lines_from"] == "2025-08-10"
    assert plan["first_emi_date"] is None
    assert detect_recurring_endpoint()["recurring"] == []


def test_a_new_booking_waiting_for_its_first_statement_is_shown(conn, make_card):
    make_card("SBI", "0077", name="Cashback")
    txn(conn, "0077", "2026-10-12", 12000.0, "#AcmeShop Internet P Bangalore IN", category="Shopping",
        merchant="AcmeShop", kind="purchase")
    conn.execute("INSERT INTO emails (gmail_id, sender, sender_email, subject, date, body_text, is_financial) "
                 "VALUES ('g-emi-2', 'SBI Card', 'booking.confirmation@sbicard.com', "
                 "'Important! Details of Merchant EMI booked on your SBI Credit Card', "
                 "'Wed, 14 Oct 2026 10:50:42 +0000', ?, 1)",
                 (SBI_BOOKING_EMAIL.replace("27/07/2026", "12/10/2026").replace("19/08/2026", "19/11/2026"),))
    conn.commit()
    (plan,) = detect_emi_plans(conn, today=TODAY)
    assert plan["status"] == "active" and plan["installments_billed"] == 0 and plan["installments_paid"] == 0
    assert plan["next_due"] == "2026-11-19" and plan["monthly_payment"] == 2163.45 and plan["card_last4"] == "0077"


SBI_BOOKING_EMAIL_2 = (
    "Dear CARDHOLDER, Thank you for booking the SBI Card Merchant EMI facility. Transaction Amount: "
    "Rs.&nbsp;12,000.00 Transaction Date(DD/MM/YYYY)): 27/07/2026 Booking Amount: Rs.&nbsp;12,000.00 "
    "Rate of Interest: 16.50&nbsp;% p.a. Tenure: 06&nbsp;months Processing Fee (Applicable Taxes): Rs.&nbsp;45.00 "
    "EMI Amount: Rs.&nbsp;2,097.35 First EMI Date(DD/MM/YYYY): 19/08/2026")


def test_paid_count_and_money_figures_cover_the_same_instalments(conn, make_card):
    """Verifier round 2: '3 of 6 paid' beside interest / principal-left figures of only the 2 instalments
    on statements. The schedule's 3rd instalment (charged 19 Oct, not yet on a statement) is now
    estimated from the plan's own rate and flagged, so every figure is 'after 3 instalments'."""
    make_card("SBI", "0066", name="Cashback")
    txn(conn, "0066", "2026-07-27", 12000.0, "#AcmeShop Internet P Bangalore IN", category="Shopping",
        merchant="AcmeShop", kind="purchase")
    txn(conn, "0066", "2026-08-19", 165.0, "INTEREST ON EMI", kind="interest")
    txn(conn, "0066", "2026-08-19", 2097.35, "FP EMI 01/06(EXCL TAX 29.70)", kind="emi_principal")
    txn(conn, "0066", "2026-09-19", 138.43, "INTEREST ON EMI", kind="interest")
    txn(conn, "0066", "2026-09-19", 2097.35, "FP EMI 02/06(EXCL TAX 24.92)", kind="emi_principal")
    conn.execute("INSERT INTO emails (gmail_id, sender, sender_email, subject, date, body_text, is_financial) "
                 "VALUES ('g-emi-3', 'SBI Card', 'booking.confirmation@sbicard.com', "
                 "'Important! Details of Merchant EMI booked on your SBI Credit Card', "
                 "'Thu, 30 Jul 2026 10:50:42 +0000', ?, 1)", (SBI_BOOKING_EMAIL_2,))
    conn.commit()
    (plan,) = detect_emi_plans(conn, today=TODAY)
    assert (plan["installments_billed"], plan["installments_paid"]) == (2, 3)
    assert (plan["estimated_installments"], plan["figures_installments"]) == (1, 3)
    third = plan["installments"][-1]
    assert (third["n"], third["date"], third["estimated"]) == (3, "2026-10-19", True)
    assert (third["interest"], third["principal"], third["tax"]) == (111.5, 1985.84, 20.07)
    assert plan["interest_paid"] == 414.93 and plan["tax_paid"] == 74.69
    assert plan["principal_paid"] == 5877.11 and plan["principal_remaining"] == 6122.89
    assert plan["monthly_payment"] == round(2097.35 + 24.92, 2)        # the latest billed instalment


def test_without_a_rate_nothing_is_estimated_and_the_figures_say_how_far_they_go(conn, make_card):
    make_card("ICICI", "0044", name="Amazon Pay")
    for x, d in enumerate(["2026-07-15", "2026-08-15"], start=1):
        txn(conn, "0044", d, 1500.0, f"Principal Amount Amortization - <{x}/6>ACME STORE 0", kind="emi_principal")
        txn(conn, "0044", d, 60.0 - 10 * x, f"Interest Amount Amortization - <{x}/6>ACME STORE 0", kind="interest")
    (plan,) = detect_emi_plans(conn, today=TODAY)       # 15 Jul .. 15 Oct charged
    assert (plan["installments_billed"], plan["installments_paid"]) == (2, 4)
    assert (plan["estimated_installments"], plan["figures_installments"]) == (0, 2)
    assert plan["interest_paid"] == 90.0


def test_two_concurrent_icici_plans_keep_their_own_interest(conn, make_card):
    """Verifier round 2 (a real card's shape): an older plan whose lines carry no marker posts on the 10th; a
    3-month plan marked <x/3> on its principal posts on the 29th. Unmarked lines used to go to a plan by
    size, so the 10th-of-month interest landed on the 3-month plan."""
    make_card("ICICI", "0042", name="Sapphiro")
    old = {"2026-05-10": (900.0, 70.0), "2026-06-10": (920.0, 60.0), "2026-07-10": (940.0, 40.0)}
    for d, (prin, intr) in old.items():
        txn(conn, "0042", d, prin, "Principal Amount Amortization - 0", kind="emi_principal")
        txn(conn, "0042", d, intr, "Interest Amount Amortization - 0", kind="interest")
    new = {"2026-05-29": (1, 1000.0, 30.0), "2026-06-29": (2, 1010.0, 20.0), "2026-07-29": (3, 1020.0, 10.0)}
    for d, (x, prin, intr) in new.items():
        txn(conn, "0042", d, prin, f"Principal Amount Amortization - <{x}/3>PAYFLIPKART 0", kind="emi_principal")
        txn(conn, "0042", d, intr, "Interest Amount Amortization - 0", kind="interest")
    plans = {p["tenure_months"]: p for p in detect_emi_plans(conn, today=TODAY)}
    three, older = plans[3], plans[None]
    assert three["interest_paid"] == 60.0 and three["principal_paid"] == 3030.0
    assert [i["date"] for i in three["installments"]] == ["2026-05-29", "2026-06-29", "2026-07-29"]
    assert older["interest_paid"] == 170.0 and older["principal_paid"] == 2760.0
    assert {i["date"][8:] for i in older["installments"]} == {"10"}
