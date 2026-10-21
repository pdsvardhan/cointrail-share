"""Categorizer substring collisions — iter-18 M14.

Descriptions are the merchant strings the parsers store (taken from the audit harness, samples.txt
and the live DB on 2026-10-20); people's names are made up but keep the collision they test
(SAMULA contains "amul", MALLESH contains "mall", DEVARAJ starts with "devara")."""
import pytest

from modules.categorizer import categorize, seed_aliases
from modules.txn_store import derive_kind


def cat(desc, ctx):
    return categorize(desc, context=ctx)[1]


# --- collisions that used to mis-file a spend -----------------------------------------------------

@pytest.mark.parametrize("desc, ctx, expected, was", [
    # audit edge cases 12.08-12.10 (HDFC "Spent Rs.X On HDFC Bank Card 9434 At <merchant> On ...")
    ("TOYOTA KIRLOSKAR SERVICE", "cc", "Uncategorized", "Travel"),          # oyo
    ("SRI SAI CHEMISTS", "cc", "Health", "EMI"),                            # emi
    ("TRENT LIMITED", "cc", "Shopping", "Rent & Housing"),                  # rent
    # "cred" inside "credit": parser placeholders and statement credit lines
    ("HSBC credit", "bank", "Uncategorized", "Finance"),
    ("Unknown credit", "bank", "Uncategorized", "Finance"),
    ("CASHBACK CREDIT AUG26-TELECOM:94~OTHERS:47", "card", "Uncategorized", "Finance"),
    # SBI e-mandate debits: "... at merchant" squeezed to "atmerchant" by the no-space pass
    ("IND*LinkedIn against E-mandate (SiHub ID - Xqzzzzz001) registered by you at merchant", "cc",
     "Subscriptions", "ATM Withdrawal"),
    ("ACMESAAS against E-mandate (SiHub ID - Ab12Cd34Ef) registered by you at merchant", "cc",
     "Uncategorized", "ATM Withdrawal"),
    ("GMR HOSPITALITY", "cc", "Uncategorized", "Health"),                   # hospital
    ("CABLE NETWORK", "bank", "Bills & Utilities", "Transport"),            # cab
    ("GOSSIP CORNER", "cc", "Uncategorized", "Investment"),                 # sip
    ("KUBERA JEWELLERS", "cc", "Shopping", "Transport"),                    # uber
    ("ACME BACK OFFICE", "cc", "Uncategorized", "Insurance"),               # acko, squeezed
    ("SAMULA RAO", "upi", "Transfer", "Food & Dining"),                     # amul
    ("MALLESH KUMAR", "upi", "Transfer", "Shopping"),                       # mall
    ("DEVARAJ K", "upi", "Transfer", "Rent & Housing"),                     # devara
])
def test_collision_words_match_only_as_words(conn, desc, ctx, expected, was):
    assert cat(desc, ctx) == expected
    assert expected != was


# --- run-together merchant strings keep working --------------------------------------------------

@pytest.mark.parametrize("desc, ctx, merchant, category", [
    ("IRCTCETICKET", "cc", "IRCTC", "Travel"),
    ("SWIGGYINSTAMART", "cc", "Swiggy Instamart", "Groceries"),
    ("RSP*INSTAMARTBANGALORE", "card", "Swiggy Instamart", "Groceries"),
    ("PYU*Swiggy FoodBangalore", "card", "Swiggy", "Food & Dining"),
    ("BLINKIT GURGAON IN", "card", "Blinkit", "Groceries"),
    ("WWWBIGBASKETCOM", "card", "BigBasket", "Groceries"),
    ("UBER INDIA SYSTEMS", "card", "Uber", "Transport"),
    ("UBERTRIP", "card", "Uber", "Transport"),
    ("OYO ROOMS", "card", "OYO", "Travel"),
    ("OYOROOMS", "card", "OYO", "Travel"),
    ("HDFC ATM", "bank", "ATM Withdrawal", "ATM Withdrawal"),
    ("CRED Club", "upi", "CRED", "Finance"),
    ("CRED", "bank", "CRED", "Finance"),
    ("CANVAI6000000000009 against E-mandate (SiHub ID - YYqzzzz018) registered by you at merchant", "cc",
     "Canva", "Subscriptions"),
    ("RAZTrendlyne against E-mandate (SiHub ID - Yqzzzzz002) registered by you at merchant", "cc",
     "Trendlyne", "Subscriptions"),
    ("Westside VIJAYAWADA IN 99", "card", "Westside", "Shopping"),
    ("TITAN COMPANY L", "cc", "Titan", "Shopping"),
    ("Avenue Supermar", "cc", "DMart", "Groceries"),
    # HDFC SMS on the Swiggy card print Swiggy's registered address
    ("SY NOS 8 1 TO 55 6 DEVARA", "cc", "Swiggy", "Food & Dining"),
    ("SY NOS 81 TO 556 DEVARABE", "cc", "Swiggy", "Food & Dining"),
    ("No 55 Sy No 8 14 Ground F", "cc", "Swiggy", "Food & Dining"),
])
def test_legitimate_merchant_strings_still_match(conn, desc, ctx, merchant, category):
    assert categorize(desc, context=ctx)[:2] == (merchant, category)


@pytest.mark.parametrize("desc, category", [
    ("EMI MOKOBARA LIFESTYLE PRIVHYDERABAD", "EMI"),
    ("OFFUS EMI,PRIN NB:07,60000000000008", "EMI"),
    ("DEEMOS HYPER3D HYPER3D.AI DE (Pay in EMIs)", "EMI"),
    ("KimsHospital Ref60000035", "Health"),
    ("CANVAS SHOE STORE", "Shopping"),
    ("RAMA FABRICS VIJAYAWADA IN 16", "Shopping"),
    ("KAMADHENU SILKS VIJAYAWADA IN 16", "Shopping"),
])
def test_keywords_both_directions(conn, desc, category):
    assert cat(desc, "card") == category


def test_merchant_behind_a_payment_gateway_wins(conn):
    assert categorize("Razorpay*Swiggy LimiteBengaluru", context="card")[:2] == ("Swiggy", "Food & Dining")
    assert categorize("Razorpay PaymentsBENGALURU", context="card")[:2] == ("Razorpay", "Pass-Through")


# --- card spends are never transfers ---------------------------------------------------------------

@pytest.mark.parametrize("desc, from_account", [
    ("paytmqr7k2m9q@ptys", "Uncategorized"),
    ("q600000034@ybl", "Uncategorized"),
    ("MANJU SRI", "Transfer"),
    ("RAMESH KUMAR", "Transfer"),
])
def test_card_spend_never_becomes_a_transfer(conn, desc, from_account):
    """RuPay credit cards on UPI pay merchants only, and a card is swiped at a shop. From a bank
    account a person's name is still a transfer; a merchant QR ID no longer is (iter-203 E4: a
    merchant UPI ID is a shop whichever account pays it)."""
    assert cat(desc, "cc") != "Transfer"
    assert cat(desc, "upi") == from_account


def test_upi_line_on_a_card_statement_takes_the_merchant(conn):
    assert cat("21:10:31 UPI-POMPEII PIZZA STUDIO", "card") == "Food & Dining"


# --- card-bill payments stay out of spend once "cred" stops matching "credit" ------------------

@pytest.mark.parametrize("desc", [
    "CREDIT CARD PAYMENT",
    "07:36:54 TELE TRANSFER CREDIT (Ref# ST600000000000000000001)",
    "BPPY PYMT DP600000000010YzTjVI (Ref# 600000000000007)",
    "DIRECT DEBIT PAYMENT(ACH)",
    "PRINCIPAL CREDIT ADJUSTMENT",
])
def test_statement_payment_credits_are_bill_payments_not_refunds(conn, desc):
    merchant, category, _ = categorize(desc, context="card")
    assert category == "Finance"
    assert derive_kind("credit", category, desc, "card") == "bill_payment"


# --- seed corrections reach an existing database ---------------------------------------------------

def test_seed_corrections_update_seed_rows_but_never_owner_rows(conn):
    # a database seeded before iter-18: the Swiggy address still filed as housing
    conn.execute("UPDATE merchant_aliases SET merchant_name='Rent/Housing', category='Rent & Housing' "
                 "WHERE pattern='devara'")
    # the owner's own alias for another pattern (POST /aliases writes source 'manual')
    conn.execute("UPDATE merchant_aliases SET merchant_name='My Flat', category='Rent & Housing', "
                 "source='manual' WHERE pattern='sy no 8 14'")
    assert cat("SY NOS 8 1 TO 55 6 DEVARA", "cc") == "Food & Dining"   # longer seed pattern, already right
    assert categorize("DEVARA", context="cc")[:2] == ("Rent/Housing", "Rent & Housing")

    seed_aliases()

    assert categorize("DEVARA", context="cc")[:2] == ("Swiggy", "Food & Dining")
    owner = conn.execute("SELECT merchant_name, category, source FROM merchant_aliases "
                         "WHERE pattern='sy no 8 14'").fetchone()
    assert tuple(owner) == ("My Flat", "Rent & Housing", "manual")
