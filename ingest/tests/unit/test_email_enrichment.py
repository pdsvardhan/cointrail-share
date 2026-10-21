"""Email enrichment must not relabel the wrong row — iter-18 M12 (enrichment.py and relink.py),
plus the amount / IST-date fixes it depends on (audit A3 13.03, 13.04, 13.09)."""
import itertools
import json

import pytest

from modules.email import enrichment, relink
from modules.email.enrichment import extract_amount_from_text, run_enrichment
from modules.txn_store import insert_transaction

_gid = itertools.count(1)
_ref = itertools.count(1)

SWIGGY_BODY = ("Delivery in 31 mins! ORDER JOURNEY Restaurant Leon Grill Your Order Summary 1 x Chicken Burger "
               "₹299.00 Item Total ₹299.00 Delivery fee ₹50.00 Grand Total ₹{total}")


def add_email(conn, sender, subject, body, date_header):
    cur = conn.execute(
        "INSERT INTO emails (gmail_id, sender, sender_email, subject, body_snippet, date, is_financial) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (f"g{next(_gid)}", sender, sender, subject, body, date_header),
    )
    conn.commit()
    return cur.lastrowid


def swiggy_email(conn, total, date_header="Mon, 19 Oct 2026 13:30:00 +0530"):
    return add_email(conn, "noreply@swiggy.in", "Your Swiggy order was successfully delivered",
                     SWIGGY_BODY.format(total=total), date_header)


def debit(conn, amount, date, desc, **kw):
    return insert_transaction(conn, amount=amount, date=date, source="sms", source_ref=f"s{next(_ref)}",
                              raw_description=desc, card_last4=kw.pop("card", "9434"),
                              context=kw.pop("context", "cc"), **kw)["id"]


def row(conn, txn_id):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()


def links(conn):
    return conn.execute("SELECT email_id, transaction_id FROM email_enrichments "
                        "WHERE transaction_id IS NOT NULL").fetchall()


# --- enrichment.run_enrichment ------------------------------------------------------------------

def test_cred_payment_next_to_a_similar_order_is_not_relabelled(conn):
    """The audit's M12 scenario: Rs 15,000 CRED bill payment, Rs 14,500 Amazon order the same day."""
    cred = debit(conn, 15000.0, "2026-10-10", "CRED CLUB", context="upi", card="6611")
    assert row(conn, cred)["category"] == "Finance"
    add_email(conn, "auto-confirm@amazon.in", 'Ordered: "Bedsheet for Double Bed..."',
              "Thanks for your order. Order Total: ₹14,500.00", "Sat, 10 Oct 2026 09:00:00 +0530")
    run_enrichment(conn)
    r = row(conn, cred)
    assert (r["category"], r["merchant"]) == ("Finance", "CRED") and links(conn) == []


def test_excluded_rows_are_never_candidates_even_at_the_exact_amount(conn):
    cred = debit(conn, 14500.0, "2026-10-10", "CRED CLUB", context="upi", card="6611")            # Finance
    own = debit(conn, 14500.0, "2026-10-10", "SELF TRANSFER TO OWN AC", context="upi", card="6611")       # Self Transfer
    paid = insert_transaction(conn, amount=14500.0, date="2026-10-10", source="sms", source_ref="bp",
                              raw_description="AUTOPAY CARD 0026", card_last4="6611", context="bank",
                              category="Uncategorized", kind="bill_payment")["id"]
    add_email(conn, "auto-confirm@amazon.in", 'Ordered: "Bedsheet for Double Bed..."',
              "Thanks for your order. Order Total: ₹14,500.00", "Sat, 10 Oct 2026 09:00:00 +0530")
    run_enrichment(conn)
    assert [row(conn, i)["category"] for i in (cred, own, paid)] == ["Finance", "Self Transfer", "Uncategorized"]
    assert links(conn) == []


def test_exact_amount_unique_debit_is_enriched(conn):
    t = debit(conn, 349.0, "2026-10-19", "BUNDL TECHNOLOGIES")
    e = swiggy_email(conn, "349.00")
    assert run_enrichment(conn)["enriched"] == 1
    r = row(conn, t)
    assert (r["merchant"], r["category"], r["confidence"]) == ("Swiggy - Leon Grill", "Food & Dining", "enriched")
    assert [tuple(x) for x in links(conn)] == [(e, t)]


@pytest.mark.parametrize("txn_amount,linked", [(348.5, True), (350.0, True), (344.0, False), (366.45, False)])
def test_amount_must_match_to_the_rupee(conn, txn_amount, linked):
    """±5% used to accept Rs 332-366 for a Rs 349 order."""
    t = debit(conn, txn_amount, "2026-10-19", "UPI PAYMENT 12345")
    swiggy_email(conn, "349.00")
    run_enrichment(conn)
    assert (row(conn, t)["confidence"] == "enriched") is linked


def test_two_candidates_is_ambiguous_and_nothing_is_relabelled(conn):
    a = debit(conn, 349.0, "2026-10-19", "UPI PAYMENT 111", card="6611", context="upi")
    b = debit(conn, 349.0, "2026-10-19", "UPI PAYMENT 222", card="9434")
    swiggy_email(conn, "349.00")
    assert run_enrichment(conn)["enriched"] == 0
    assert all(row(conn, i)["confidence"] != "enriched" for i in (a, b)) and links(conn) == []


def test_window_is_one_ist_day(conn):
    # 19:00 UTC on 18 Oct is 00:30 IST on 19 Oct: the 20th is in the window, the 17th is not
    late = debit(conn, 349.0, "2026-10-20", "UPI PAYMENT 333")
    swiggy_email(conn, "349.00", "Sat, 18 Oct 2026 19:00:00 +0000")
    early = debit(conn, 512.0, "2026-10-17", "UPI PAYMENT 444")
    swiggy_email(conn, "512.00", "Sat, 18 Oct 2026 19:05:00 +0000")
    run_enrichment(conn)
    assert row(conn, late)["confidence"] == "enriched"
    assert row(conn, early)["confidence"] != "enriched"


def test_manual_override_is_linked_but_never_relabelled(conn):
    t = debit(conn, 349.0, "2026-10-19", "UPI PAYMENT 555")
    conn.execute("UPDATE transactions SET merchant = 'Team lunch', category = 'Work Expense' WHERE id = ?", (t,))
    conn.execute("INSERT INTO category_overrides (transaction_id, old_category, new_category) "
                 "VALUES (?, 'Uncategorized', 'Work Expense')", (t,))
    m = debit(conn, 512.0, "2026-10-19", "UPI PAYMENT 666")
    conn.execute("UPDATE transactions SET category = 'Gifts', confidence = 'manual' WHERE id = ?", (m,))
    swiggy_email(conn, "349.00")
    swiggy_email(conn, "512.00")
    run_enrichment(conn)
    assert (row(conn, t)["merchant"], row(conn, t)["category"]) == ("Team lunch", "Work Expense")
    assert (row(conn, m)["category"], row(conn, m)["confidence"]) == ("Gifts", "manual")
    assert {x["transaction_id"] for x in links(conn)} == {t, m}


# --- amount / date helpers ----------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Order Total: ₹1,23,456.78", 123456.78),                                           # 13.01
    ("Amount Paid: ₹ 499", 499.0),                                                      # 13.02
    ("You Paid ₹245.5 for your ride", 245.5),                                           # 13.03 (was 245)
    ("Fare breakup: Ride fare ₹120.00, Taxes ₹11.00. Total ₹131.00", 131.0),            # 13.04 (was 120)
    ("Item Total ₹300 Delivery fee ₹49 Grand Total ₹349", 349.0),                       # 13.05
    ("Your bill: Rs. 1,234.00", 1234.0),                                                # 13.06
    ("Order #40412345 confirmed. Total: INR 1,299.00", 1299.0),                         # 13.07
    ("Item Total ₹300.00 Sub Total ₹320.00 Total ₹369.00", 369.0),
])
def test_amount_extraction(text, expected):
    assert extract_amount_from_text(text) == expected


@pytest.mark.parametrize("header,expected", [
    ("Sun, 19 Oct 2026 00:30:00 +0530", "2026-10-19"),                                  # 13.08
    ("Sat, 18 Oct 2026 19:00:00 +0000", "2026-10-19"),                                  # 13.09 (was 18th)
    ("Mon, 19 Oct 2026 14:20:56 +0000 (UTC)", "2026-10-19"),                            # 13.10
    ("2026-10-19T01:00:00", "2026-10-19"),
])
def test_email_dates_are_ist_days(header, expected):
    assert enrichment._parse_email_date(header) == expected
    assert relink._parse_email_date(header) == expected


# --- relink.py: the same class of problem --------------------------------------------------------

def orphan(conn, brand, extracted, date_header, sender="noreply@swiggy.in"):
    eid = add_email(conn, sender, f"Your {brand} order was successfully delivered", "", date_header)
    conn.execute("INSERT INTO email_enrichments (email_id, transaction_id, enrichment_type, brand, extracted_data) "
                 "VALUES (?, NULL, 'food_order', ?, ?)", (eid, brand, json.dumps(extracted)))
    conn.commit()
    return eid


def test_relink_links_the_one_candidate(conn):
    t = debit(conn, 410.0, "2026-10-18", "SWIGGY")
    orphan(conn, "Swiggy", {"brand": "Swiggy", "restaurant": "Leon Grill", "amount": 410.0},
           "Mon, 19 Oct 2026 10:00:00 +0530")
    assert relink.link_orphan_enrichments(conn)["total_linked"] == 1
    assert (row(conn, t)["merchant"], row(conn, t)["category"]) == ("Swiggy - Leon Grill", "Food & Dining")


def test_relink_skips_when_several_orders_fit(conn):
    """It used to take the closest one, so an order email could relabel a different order."""
    a = debit(conn, 410.0, "2026-10-19", "SWIGGY")
    b = debit(conn, 410.0, "2026-10-18", "SWIGGY")
    orphan(conn, "Swiggy", {"brand": "Swiggy", "restaurant": "Leon Grill", "amount": 410.0},
           "Mon, 19 Oct 2026 10:00:00 +0530")
    assert relink.link_orphan_enrichments(conn)["total_linked"] == 0
    assert row(conn, a)["merchant"] == "Swiggy" and row(conn, b)["merchant"] == "Swiggy"


def test_relink_never_touches_a_finance_row_and_respects_a_stated_amount(conn):
    bill = insert_transaction(conn, amount=8167.11, date="2026-10-08", source="sms", source_ref="pay1",
                              raw_description="AMAZON PAY ICICI CARD BILL", merchant="Amazon Pay ICICI",
                              category="Finance", card_last4="6611", context="upi")["id"]
    orphan(conn, "Amazon", {"brand": "Amazon", "products": [{"name": "Umbrella"}]},
           "Thu, 08 Oct 2026 12:00:00 +0530", sender="auto-confirm@amazon.in")
    order = debit(conn, 650.0, "2026-10-12", "AMAZON PAY INDIA")
    orphan(conn, "Amazon", {"brand": "Amazon", "order_total": 499.0, "products": [{"name": "Soap"}]},
           "Tue, 13 Oct 2026 12:00:00 +0530", sender="auto-confirm@amazon.in")
    relink.link_orphan_enrichments(conn)
    assert (row(conn, bill)["category"], row(conn, bill)["merchant"]) == ("Finance", "Amazon Pay ICICI")
    assert row(conn, order)["merchant"] == "Amazon"                      # Rs 650 is not the Rs 499 order
    assert links(conn) == []


def test_relink_and_upgrade_leave_manual_rows_labelled(conn):
    t = debit(conn, 410.0, "2026-10-18", "SWIGGY")
    conn.execute("INSERT INTO category_overrides (transaction_id, old_category, new_category) "
                 "VALUES (?, 'Food & Dining', 'Work Expense')", (t,))
    conn.execute("UPDATE transactions SET category = 'Work Expense' WHERE id = ?", (t,))
    orphan(conn, "Swiggy", {"brand": "Swiggy", "restaurant": "Leon Grill", "amount": 410.0},
           "Mon, 19 Oct 2026 10:00:00 +0530")
    relink.run_relink(conn)
    r = row(conn, t)
    assert (r["merchant"], r["category"]) == ("Swiggy", "Work Expense")
    assert [x["transaction_id"] for x in links(conn)] == [t]             # linked for the order details only


# --- verifier round 2: promotions, non-money numbers, merchant-only relinks ------------------------

@pytest.mark.parametrize("text", ["Delivered in 3 hrs 2 mins", "Arriving today, fare 3 per km", "Total 2 items"])
def test_numbers_without_a_currency_are_not_amounts(text):
    assert extract_amount_from_text(text) is None


@pytest.mark.parametrize("subject,promo", [
    ("₹19 deals on your favourites", True), ("Rides from ₹3/trip this weekend", True),
    ("Flat 60% off on Instamart", True), ("Your Swiggy order was successfully delivered", False),
    ('Ordered: "Bedsheet for Double Bed..."', False),
])
def test_promotional_subjects(subject, promo):
    assert enrichment.is_promotional(subject) is promo


def test_a_promotion_never_relabels_a_small_upi_payment(conn):
    chaat = debit(conn, 20.0, "2026-10-12", "SRI SAI CHAAT", context="upi", card="6611")
    add_email(conn, "noreply@swiggy.in", "₹19 deals on your favourites", "Order now ₹20 off", "Mon, 12 Oct 2026 12:00:00 +0530")
    run_enrichment(conn)
    assert row(conn, chaat)["merchant"] != "Swiggy" and links(conn) == []


def test_tiny_amounts_are_never_matched(conn):
    tea = debit(conn, 3.0, "2026-10-12", "TEA STALL", context="upi", card="6611")
    add_email(conn, "noreply@uber.com", "Your trip receipt", "Total ₹3.00", "Mon, 12 Oct 2026 12:00:00 +0530")
    run_enrichment(conn)
    assert row(conn, tea)["merchant"] != "Uber" and links(conn) == []


def test_relink_needs_the_email_amount_and_keeps_a_specific_category(conn):
    groceries = debit(conn, 357.0, "2026-09-30", "SwiggyInstamart")
    assert row(conn, groceries)["category"] == "Groceries"
    orphan(conn, "Swiggy", {"brand": "Swiggy", "restaurant": "Leon Grill"}, "Wed, 30 Sep 2026 20:00:00 +0530")
    assert relink.link_orphan_enrichments(conn)["total_linked"] == 0        # no amount: no link
    orphan(conn, "Swiggy", {"brand": "Swiggy", "restaurant": "Leon Grill", "amount": 357.0},
           "Wed, 30 Sep 2026 20:00:00 +0530")
    relink.link_orphan_enrichments(conn)
    assert row(conn, groceries)["category"] == "Groceries"                  # detail added, category kept


# --- verifier reverify2: only order emails, never refunds / fee lines / another brand -------------

def test_marketing_email_never_labels_a_payment(conn):
    """A Swiggy marketing email ('mornings starting at just Rs 39') relabelled an Airtel Rs 39
    payment as Swiggy / Food (#3610)."""
    airtel = debit(conn, 39.0, "2026-10-19", "AIRTEL PAYMENTS", context="upi", card="4400")
    add_email(conn, "noreply@swiggy.in", "Your breakfast deserves better",
              "Fresh mornings starting at just ₹39. Tap to know more", "Mon, 19 Oct 2026 08:00:00 +0530")
    run_enrichment(conn)
    assert row(conn, airtel)["merchant"] != "Swiggy" and links(conn) == []


@pytest.mark.parametrize("subject", [
    "Alert : Payment Failed for your Order 12345",
    "Your refund for Cloth Fusion Kurta has been processed",
    "Hey there! We want to hear you.",
    "Help make Amazon Pay better. Your feedback matters to us.",
    "Sam Lee paid you INR 450.00 on Splitwise",
])
def test_non_order_subjects_are_never_matched(subject):
    assert enrichment.is_order_email(subject) is False


@pytest.mark.parametrize("subject", [
    "Your Swiggy order was successfully delivered",
    "Your Instamart order was successfully delivered",
    "Your Zomato order from Chennapatnam Tiffins",
    "Your Saturday evening trip with Uber",
    'Delivered: 1 item | Order # 402-1234567-1234567',
    'Ordered: "Ghar Soaps Sandalwood ..."',
    "Your Swiggy Dineout payment was successful",
    "redBus Ticket - TV1234567",
])
def test_order_subjects_are_matched(subject):
    assert enrichment.is_order_email(subject) is True


def test_amazon_refund_email_never_labels_a_swiggy_debit(conn):
    swiggy = debit(conn, 716.0, "2026-10-19", "RAZ*SwiggyBangalore")
    add_email(conn, "auto-confirm@amazon.in", "Your refund for Cloth Fusion Kurta",
              "Refund total ₹717.00", "Mon, 19 Oct 2026 12:00:00 +0530")
    run_enrichment(conn)
    assert links(conn) == [] and "Amazon" not in (row(conn, swiggy)["merchant"] or "")


def test_an_order_email_never_labels_a_debit_naming_another_brand(conn):
    blinkit = debit(conn, 410.0, "2026-10-19", "BLINKIT GURGAON")
    swiggy_email(conn, 410.0)
    run_enrichment(conn)
    assert links(conn) == [] and row(conn, blinkit)["merchant"] != "Swiggy - Leon Grill"


def test_fee_and_interest_lines_are_not_order_candidates(conn):
    fee = insert_transaction(conn, amount=90.0, date="2026-10-19", source="card", source_ref="st#9",
                             raw_description="IGST-CI@18%", card_last4="9434", context="card")["id"]
    assert row(conn, fee)["kind"] == "fee"
    swiggy_email(conn, 90.0)
    run_enrichment(conn)
    assert links(conn) == [] and row(conn, fee)["merchant"] != "Swiggy - Leon Grill"


def test_instamart_email_from_swiggy_is_groceries(conn):
    t = debit(conn, 344.0, "2026-10-19", "PAYU*INSTAMART")
    add_email(conn, "noreply@swiggy.in", "Your Instamart order was successfully delivered",
              "Your order summary Grand Total ₹344.00", "Mon, 19 Oct 2026 13:30:00 +0530")
    run_enrichment(conn)
    run_enrichment(conn)                                   # stored once, not again as 'Swiggy'
    r = row(conn, t)
    assert r["category"] == "Groceries" and r["merchant"].startswith("Swiggy Instamart")
    brands = [x["brand"] for x in conn.execute("SELECT brand FROM email_enrichments")]
    assert brands == ["Swiggy Instamart"]


def _old_link(conn, eid, tid, brand="Swiggy", extracted=None, label=("Swiggy - Leon Grill", "Food & Dining")):
    """A link as the old matcher left it."""
    conn.execute("INSERT INTO email_enrichments (email_id, transaction_id, enrichment_type, brand, extracted_data) "
                 "VALUES (?, ?, 'food_order', ?, ?)", (eid, tid, brand, json.dumps(extracted or {})))
    if tid is not None:
        conn.execute("UPDATE transactions SET merchant = ?, category = ?, confidence = 'enriched' WHERE id = ?",
                     (label[0], label[1], tid))
    conn.commit()


def test_rebuild_undoes_old_wrong_links_and_restores_labels(conn):
    """Links made by the old rules (a marketing email) are undone and the row gets the
    categoriser's labels back; a valid link is re-made; a dry run writes nothing."""
    bad = debit(conn, 39.0, "2026-10-19", "AIRTEL PAYMENTS", context="upi", card="4400")
    good = debit(conn, 410.0, "2026-10-19", "SWIGGY")
    promo = add_email(conn, "noreply@swiggy.in", "Your breakfast deserves better", "starting at just ₹39",
                      "Mon, 19 Oct 2026 08:00:00 +0530")
    order = swiggy_email(conn, 410.0)
    _old_link(conn, promo, bad)
    _old_link(conn, order, good, extracted={"restaurant": "Leon Grill"})
    dry = enrichment.rebuild_enrichment_links(conn, dry_run=True)
    assert (dry["links_before"], dry["links_after"], dry["kept"], dry["dropped"]) == (2, 1, 1, 1)
    assert row(conn, bad)["merchant"] == "Swiggy - Leon Grill" and len(links(conn)) == 2   # nothing written
    enrichment.rebuild_enrichment_links(conn, dry_run=False)
    assert [x["transaction_id"] for x in links(conn)] == [good]
    assert row(conn, bad)["category"] != "Food & Dining" and row(conn, bad)["confidence"] != "enriched"
    assert row(conn, good)["merchant"] == "Swiggy - Leon Grill"
    again = enrichment.rebuild_enrichment_links(conn, dry_run=True)
    assert (again["kept"], again["moved"], again["new"], again["dropped"], again["relabelled"]) == (1, 0, 0, 0, 0)


def test_rebuild_moves_a_link_off_a_merged_copy_to_the_live_row(conn, make_card):
    """The old link sat on an SMS row that was later merged into its statement line; the visible
    row is the statement line, so that is where the order's labels must be."""
    make_card("HDFC", "9434")
    sms = debit(conn, 344.0, "2026-10-19", "PAYU*INSTAMART")
    line = insert_transaction(conn, amount=344.0, date="2026-10-19", source="card", source_ref="st#4",
                              raw_description="PAYU*INSTAMART BANGALORE", card_last4="9434", context="card")["id"]
    assert row(conn, sms)["is_duplicate"] == 1
    eid = add_email(conn, "noreply@swiggy.in", "Your Instamart order was successfully delivered",
                    "Your order summary Grand Total ₹344.00", "Mon, 19 Oct 2026 13:30:00 +0530")
    _old_link(conn, eid, sms, extracted={"items": [{"name": "Curd"}]})
    res = enrichment.rebuild_enrichment_links(conn, dry_run=False)
    assert res["moved"] == 1 and [x["transaction_id"] for x in links(conn)] == [line]
    assert row(conn, line)["category"] == "Groceries"
    assert [x["brand"] for x in conn.execute("SELECT brand FROM email_enrichments")] == ["Swiggy Instamart"]


def test_rebuild_matches_emails_the_old_matcher_left_unlinked(conn):
    t = debit(conn, 410.0, "2026-10-19", "SWIGGY")
    eid = swiggy_email(conn, 410.0)
    _old_link(conn, eid, None)
    res = enrichment.rebuild_enrichment_links(conn, dry_run=False)
    assert res["new"] == 1 and [x["transaction_id"] for x in links(conn)] == [t]


def test_one_debit_is_explained_by_one_order_email(conn):
    """Two Rs 262 orders from one restaurant on one day and a single Rs 262 debit: the second
    order email must not relabel the debit the first one already explains."""
    t = debit(conn, 262.0, "2026-10-19", "SWIGGY")
    swiggy_email(conn, 262.0, "Mon, 19 Oct 2026 13:30:00 +0530")
    swiggy_email(conn, 262.0, "Mon, 19 Oct 2026 20:10:00 +0530")
    run_enrichment(conn)
    assert [x["transaction_id"] for x in links(conn)] == [t]
    res = enrichment.rebuild_enrichment_links(conn, dry_run=False)
    assert res["links_after"] == 1


UBER_BODY = ("Total ₹276.24 October 1, 2025 Thanks for riding, Sam. Total ₹276.24 Suggested fare ₹276.24 "
             "Subtotal ₹276.24 Payments Cash 10/1/25 6:16 PM ₹276.24")


def test_uber_receipt_matches_a_payment_to_the_driver_only_around_the_trip(conn):
    """The driver is paid by UPI to a person's name. A same-amount payment to someone a day away
    from the trip is not the trip (Rs 330 paid the day before, Rs 700 the day after)."""
    driver = debit(conn, 276.0, "2025-10-01", "RAVI KUMAR", context="upi", card="4400",
                   occurred_at="2025-10-01T18:16:40+05:30")
    elsewhere = debit(conn, 276.0, "2025-10-02", "ANIL KUMAR", context="upi", card="4400",
                      occurred_at="2025-10-02T14:00:00+05:30")
    add_email(conn, "noreply@uber.com", "Your Wednesday evening trip with Uber", UBER_BODY,
              "Wed, 01 Oct 2025 18:16:39 +0530")
    run_enrichment(conn)
    assert [x["transaction_id"] for x in links(conn)] == [driver]
    assert (row(conn, driver)["merchant"], row(conn, driver)["category"]) == ("Uber", "Transport")
    assert row(conn, elsewhere)["merchant"] != "Uber"


def test_uber_receipt_never_takes_a_rounded_up_payment(conn):
    """Rs 280 for a Rs 276.24 fare is outside the email's own amount (+/- Rs 1)."""
    driver = debit(conn, 280.0, "2025-10-01", "RAVI KUMAR", context="upi", card="4400",
                   occurred_at="2025-10-01T18:16:40+05:30")
    add_email(conn, "noreply@uber.com", "Your Wednesday evening trip with Uber", UBER_BODY,
              "Wed, 01 Oct 2025 18:16:39 +0530")
    run_enrichment(conn)
    assert links(conn) == [] and row(conn, driver)["merchant"] != "Uber"


def test_uber_charge_naming_uber_matches_by_date(conn):
    card = debit(conn, 276.24, "2025-10-01", "UBER INDIA SYSTEMS")
    add_email(conn, "noreply@uber.com", "Your Wednesday evening trip with Uber",
              UBER_BODY.replace("Payments Cash", "Payments Visa ••1234"), "Wed, 01 Oct 2025 18:16:39 +0530")
    run_enrichment(conn)
    assert [x["transaction_id"] for x in links(conn)] == [card]


def test_merchant_labels_decode_html_entities(conn):
    t = debit(conn, 410.0, "2026-10-19", "ZOMATO LTD")
    add_email(conn, "noreply@zomato.com", "Your Zomato order from Domino&#39;s Pizza",
              "Total paid ₹410.00", "Mon, 19 Oct 2026 13:30:00 +0530")
    run_enrichment(conn)
    assert "&#39;" not in (row(conn, t)["merchant"] or "")


def test_an_order_link_follows_the_statement_line_that_wins_the_merge(conn, make_card):
    """The SMS was enriched first; a month later the statement line arrives and wins — the visible
    row must keep the order's link and labels."""
    make_card("HDFC", "9434")
    sms = debit(conn, 410.0, "2026-10-19", "SWIGGY")
    swiggy_email(conn, 410.0)
    run_enrichment(conn)
    assert [x["transaction_id"] for x in links(conn)] == [sms]
    line = insert_transaction(conn, amount=410.0, date="2026-10-19", source="card", source_ref="st#7",
                              raw_description="SWIGGY BANGALORE", card_last4="9434", context="card")["id"]
    assert row(conn, sms)["is_duplicate"] == 1
    assert [x["transaction_id"] for x in links(conn)] == [line]
    assert (row(conn, line)["merchant"], row(conn, line)["category"]) == ("Swiggy - Leon Grill", "Food & Dining")


def test_of_two_same_amount_orders_the_earlier_email_explains_the_payment(conn):
    t = debit(conn, 262.0, "2026-01-29", "SWIGGY")
    late = swiggy_email(conn, 262.0, "Thu, 29 Jan 2026 22:10:00 +0530")       # stored first, sent later
    early = swiggy_email(conn, 262.0, "Thu, 29 Jan 2026 19:05:00 +0530")
    run_enrichment(conn)
    assert [(x["email_id"], x["transaction_id"]) for x in links(conn)] == [(early, t)]


def test_rebuild_also_files_emails_never_enriched(conn):
    t = debit(conn, 410.0, "2026-10-19", "SWIGGY")
    swiggy_email(conn, 410.0)                     # synced after the last hourly pass: no enrichment row
    res = enrichment.rebuild_enrichment_links(conn, dry_run=False)
    assert res["links_after"] == 1 and [x["transaction_id"] for x in links(conn)] == [t]
    assert enrichment.rebuild_enrichment_links(conn, dry_run=True)["relabelled"] == 0


@pytest.mark.parametrize("subject,body,expected", [
    ("Your Zomato order from Chennapatnam Tiffins", "", "Chennapatnam Tiffins"),
    ("Order update", "Your order from Tossin Pizza ORDER ID: 7700001 was delivered", "Tossin Pizza"),
])
def test_zomato_restaurant_never_carries_the_order_id(subject, body, expected):
    assert enrichment.extract_restaurant_from_zomato(subject, body) == expected


def test_labels_decode_html_entities():
    assert enrichment._label("Swiggy", {"restaurant": "Domino&#39;s"}) == "Swiggy - Domino's"


def test_an_order_email_whose_debit_arrives_later_is_linked_by_a_later_hourly_pass(conn):
    """The statement line (or an SMS recovered by the backfill) can arrive weeks after the email."""
    from datetime import datetime, timedelta, timezone
    sent = datetime.now(timezone.utc) - timedelta(days=5)
    swiggy_email(conn, 410.0, sent.strftime("%a, %d %b %Y %H:%M:%S +0000"))
    assert run_enrichment(conn)["enriched"] == 0 and links(conn) == []
    t = debit(conn, 410.0, sent.strftime("%Y-%m-%d"), "SWIGGY")
    assert run_enrichment(conn)["enriched"] == 1
    assert [x["transaction_id"] for x in links(conn)] == [t]


def test_old_unlinked_order_emails_are_not_retried_forever(conn):
    swiggy_email(conn, 410.0, "Sun, 05 Jan 2025 13:30:00 +0530")
    run_enrichment(conn)
    debit(conn, 410.0, "2025-01-05", "SWIGGY")
    assert run_enrichment(conn)["enriched"] == 0          # older than RETRY_DAYS (400): the rebuild's job
    assert enrichment.rebuild_enrichment_links(conn, dry_run=False)["links_after"] == 1


@pytest.mark.parametrize("journey,expected", [
    ("Leaf Cafe SYNO. 12/3/A (SY NO.1 Old) SOME VILLAGE, Hyderabad Oct 17, 10:52 PM Sam Lee Flat 201, Block 3",
     "Leaf Cafe"),
    ("Cravery Unit L1-04,First Floor,Plot No.43 Oct 13, 9:58 PM Sam Lee", "Cravery"),
    ("Pista House 4-20, 20/HIG-A, Main Road Apr 18, 7:15 PM Sam Lee", "Pista House"),
    ("Yum Yum Tree Plot No: 12, Road 3", "Yum Yum Tree"),
    ("4 NY Smash Burgers Plot 12", "4 NY Smash Burgers"),
])
def test_swiggy_journey_block_gives_only_the_restaurant(journey, expected):
    body = f"Order delivered ORDER JOURNEY {journey} Your Order Summary 1 x Item"
    assert enrichment.extract_restaurant_from_swiggy("Your Swiggy order was successfully delivered", body) == expected


def test_swiggy_classic_format_still_reads():
    body = "Restaurant Leon Grill Your Order Summary 1 x Chicken Burger"
    assert enrichment.extract_restaurant_from_swiggy("Your Swiggy order was delivered", body) == "Leon Grill"


SWIGGY_2026 = ("Delivery in 27 mins! ₹239 saved on this order ₹87 saved with ₹152 saved with Promotional Savings "
               "ORDER JOURNEY Kadapa Dosa House Shop No- 2-48/P/21 Ground Floor, Sample Nagar, Hyderabad,500032. "
               "Jun 29, 1:08 AM Sam Lee Flat 9, Block 1, Sample Road, Hyderabad Jun 29, 1:35 AM Order Items 1 x Dosa "
               "₹120 Paid Via Credit/Debit card ₹345.00 Disclaimer : Attached is the invoice")


@pytest.mark.parametrize("body,expected", [
    (SWIGGY_2026, 345.0),
    (SWIGGY_2026.replace("₹345.00", "₹663"), 663.0),
    ("Your Dineout bill Total Bill ₹1,200.00 Discount ₹220.00 Total Paid ₹980.00", 980.0),
    ("₹239 saved on this order ₹87 saved with coupon", None),
    ("Thanks for dining with us. You paid ₹980.", 980.0),
])
def test_the_amount_paid_wins_over_savings_and_bill_totals(body, expected):
    assert extract_amount_from_text(body) == expected


@pytest.mark.parametrize("journey,expected", [
    ("Leon&#39;s - Burgers &amp; Wings (Leon Grill) MYSCAPE STORIES GROUND FLOOR UNIT NO 5", "Leon's - Burgers & Wings (Leon Grill)"),
    ("Chennapatnam Tiffins CHENNAPATNAM, Road 3", "Chennapatnam Tiffins"),
    ("Gourmet Shawarma V SAMPLE PERSON,PLOT 12", "Gourmet Shawarma"),
    ("Kadapa Dosa House Shop No- 2-48/P/21 Ground Floor", "Kadapa Dosa House"),
])
def test_journey_names_stop_at_the_address(journey, expected):
    body = f"saved ORDER JOURNEY {journey} Sep 6, 1:08 AM Sam Lee"
    assert enrichment.extract_restaurant_from_swiggy("Your Swiggy order was delivered", body) == expected


@pytest.mark.parametrize("name", ["Minerva Coffee Shop", "Roast 24 Seven", "Mandi @36"])
def test_classic_format_names_are_kept_exactly(name):
    body = f"Delivery in 31 mins! ORDER SUMMARY Restaurant {name} Your Order Summary 1 x Item ₹100"
    assert enrichment.extract_restaurant_from_swiggy("Your Swiggy order was delivered", body) == name
