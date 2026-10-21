"""
Email enrichment engine.
Scans email receipts to enrich transaction data with brand, restaurant, ride details.
Includes Swiggy item parser, Amazon product parser, and generic merchant enrichment.
"""
import html
import re
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from db import get_db
from util.timez import IST, parse_stored_timestamp


def _parse_email_date(date_str):
    """Parse email date string to YYYY-MM-DD, handling both ISO and RFC 2822 formats.

    RFC 2822 headers are converted to the IST calendar day: most Gmail Date headers are +0000,
    so 'Sat, 18 Oct 2026 19:00:00 +0000' is an order placed at 00:30 on 19 Oct in India
    (iter-18 M8 / audit A3 13.09)."""
    if not date_str:
        return None
    # Already YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return date_str[:10]
    # Try RFC 2822 (e.g. "Wed, 8 Oct 2025 12:25:41 +0000")
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        pass
    # Try common datetime formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str[:19], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None

# Brand email patterns: (sender_pattern, brand, enrichment_type, category)
BRAND_EMAILS = [
    ("rapido", "Rapido", "ride_receipt", "Transport"),
    ("uber.com", "Uber", "ride_receipt", "Transport"),
    ("olacabs.com", "Ola", "ride_receipt", "Transport"),
    ("ola.com", "Ola", "ride_receipt", "Transport"),
    ("swiggy.in", "Swiggy", "food_order", "Food & Dining"),
    ("swiggy.com", "Swiggy", "food_order", "Food & Dining"),
    ("toingit.com", "Toing", "food_order", "Food & Dining"),
    ("zomato.com", "Zomato", "food_order", "Food & Dining"),
    ("splitwise.com", "Splitwise", "splitwise_settle", "Miscellaneous"),
    ("bookmyshow.com", "BookMyShow", "entertainment", "Entertainment"),
    ("blinkit", "Blinkit", "grocery_order", "Groceries"),
    ("zepto", "Zepto", "grocery_order", "Groceries"),
    ("instamart", "Swiggy Instamart", "grocery_order", "Groceries"),
    ("redbus.in", "RedBus", "travel_booking", "Travel"),
    ("amazon.in", "Amazon", "shopping_order", "Shopping"),
    ("amazon.com", "Amazon", "shopping_order", "Shopping"),
    ("flipkart.com", "Flipkart", "shopping_order", "Shopping"),
]

# Paise may be printed with one digit ("₹245.5"); \d{2} silently dropped them (audit A3 13.03),
# and matching is now to the rupee, so the amount has to be the real one.
_NUM = r'([\d,]+(?:\.\d{1,2})?)'
# A currency marker as a whole token: case-insensitive "Rs" also matches inside "hrs" ("Delivered in
# 3 hrs 2 mins" was read as Rs 2 and relabelled a Rs 2 payment, iter-18 verifier).
_CUR = r'(?:(?<![A-Za-z])(?:Rs\.?|INR)|₹)'


def extract_amount_from_text(text):
    """Try to extract an amount from email body/subject."""
    if not text:
        return None
    # Priority patterns, in strict precedence (each searched on its own, so an earlier label in the
    # text cannot win over a better one later): what was actually paid first. Swiggy's 2026 format
    # prints no total, only "Paid Via Credit/Debit card Rs 345" after "Rs 239 saved on this order",
    # and Dineout prints "Total Bill" (before discounts) ahead of "Total Paid" (iter-18 verifier).
    priority_patterns = [
        r'Paid\s+Via\b[^₹\d]{0,40}?' + _CUR + r'\s*' + _NUM,
        r'(?:Total\s*Paid|Amount\s*Paid|You\s*Paid|Net\s*Payable|Paid\s*Amount)[:\s]*' + _CUR + r'\s*' + _NUM,
        r'(?:Grand\s*Total|Order\s*Total|Total\s*Amount)[:\s]*' + _CUR + r'\s*' + _NUM,
        r'Total\s*Bill[:\s]*' + _CUR + r'\s*' + _NUM,
    ]
    for p in priority_patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
    # A plain "Total ₹131.00" is the bill; the components printed before it ("Ride fare ₹120.00,
    # Taxes ₹11.00") are not (audit A3 13.04). "Item Total" / "Sub Total" are components too.
    totals = re.findall(r'(?<![a-z])(?<!item )(?<!sub )(?<!sub)total[:\s]*' + _CUR + r'\s*' + _NUM,
                        text, re.IGNORECASE)
    if totals:
        try:
            return float(totals[-1].replace(",", ""))
        except ValueError:
            pass
    # General patterns. A currency marker is required: without one, "Delivered in 3 hrs" or a
    # "₹3/trip" promo's bare figures were read as amounts and relabelled Rs 2-20 payments (iter-18
    # verifier, M12).
    not_saved = r'(?![\d,]|\.\d)(?!\s*(?:saved|off\b|discount|cashback))'      # "Rs 239 saved on this order"
    patterns = [
        r'(?:amount|total|paid|fare|bill)[:\s]*' + _CUR + r'\s*' + _NUM + not_saved,
        _CUR + r'\s*' + _NUM + not_saved,
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
    return None

_ADDRESS_WORD = re.compile(
    r"^(?:unit|plot|flat|door|shop|block|floor|ground|first|second|third|syno\.?|sy\.?|survey|no[.:-]?|h\.?no\.?|"
    r"opp\.?|opposite|near|beside|behind|#|building|bldg\.?|complex|towers?|apartments?|plaza|road|rd\.?|street|"
    r"lane|cross|layout|phase|sector|colony|nagar|village)$", re.I)
_MONTH_TIME = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},\s*\d{1,2}:\d{2}", re.I)
RESTAURANT_MAX_CHARS = 40


def _restaurant_name(text):
    """The restaurant's name from the start of Swiggy's 'ORDER JOURNEY' block, never its address.

    The block (since 2026-03) runs restaurant, its street address, the order time and the
    customer's own name and address together ("AnTeRa Courtyard SYNO. 251/8/E/1 ... Oct 17, 10:52 PM
    <name> Flat 201, ..."); the old pattern kept going into the address (iter-18 verifier). The name
    stops at the first address-like word (Shop / Unit / Plot / SYNO / Building / Road ...), a number
    after the first word, a comma, a date and time, or a switch from normal case to an ALL-CAPS word
    ("Chennapatnam Tiffins CHENNAPATNAM", "Gourmet Shawarma V RAJATH ..."); whole words only, capped."""
    text = html.unescape(_MONTH_TIME.split(text or "", maxsplit=1)[0])
    words = []
    for i, w in enumerate(text.split()):
        bare = w.rstrip(",")
        letters = re.sub(r"[^A-Za-z]", "", bare)
        if _ADDRESS_WORD.match(bare) or (i > 0 and re.search(r"\d", bare)):
            break
        if (len(letters) >= 2 and letters.isupper() and any(re.search(r"[a-z]", x) for x in words)):
            break                                   # the restaurant's ALL-CAPS street address begins
        if len(" ".join(words + [bare])) > RESTAURANT_MAX_CHARS:
            break                                   # whole words only
        words.append(bare)
        if w.endswith(","):
            break
    while words and (len(re.sub(r"[^A-Za-z]", "", words[-1])) <= 1 or words[-1] in ("-", "&", "and")):
        words.pop()                                 # a stray initial or joiner left at the end
    name = " ".join(words).strip(" .,-")
    return name or None


def extract_restaurant_from_swiggy(subject, body):
    """Extract restaurant name from Swiggy order email."""
    combined = ((subject or "") + " " + (body or "")).lower()
    # Skip Instamart orders - they don't have restaurant names
    if "instamart" in combined:
        return None
    # Patterns ordered by specificity - match actual Swiggy email formats
    patterns = [
        # "Restaurant Chennapatnam Tiffins Your Order Summary" format (most common)
        r'Restaurant\s+(.+?)\s+Your Order Summary',
        # "ORDER JOURNEY <restaurant> <its address> <time> <customer>" (restaurant cut by _restaurant_name)
        r'ORDER JOURNEY\s+(.{1,200})',
        # "Ordered from: Restaurant Name <address>" format
        r'Ordered from:\s+(.+?)(?:\s+\d|,\s*(?:Plot|Door|Flat|Block|Floor|Unit|Shop|Beside|Near|Opp|Ground|First|Sec|Third|\d+(?:st|nd|rd|th)))',
        # Legacy fallback patterns
        r'order from\s+(.+?)\s+(?:has been|is on|was|is being)',
        r'from\s+(.+?)\s+(?:delivered|confirmed|placed)',
    ]
    for text in [body or "", subject or ""]:
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                if p.startswith("ORDER JOURNEY"):
                    name = _restaurant_name(m.group(1))
                else:
                    name = re.sub(r'[\s,]+$', '', m.group(1).strip())
                if name and len(name) > 1:
                    return name
    return None

def extract_restaurant_from_zomato(subject, body):
    """Extract restaurant name from Zomato order email."""
    m = re.search(r'order from\s+(.+?)\s*$', subject or "", re.IGNORECASE)
    if m:
        return _strip_order_id(m.group(1))
    patterns = [
        r'order from\s+(.+?)\s+(?:has been|is|was)',
        r'from\s+(.+?)\s+(?:delivered|confirmed)',
    ]
    for p in patterns:
        m = re.search(p, body or "", re.IGNORECASE)
        if m:
            return _strip_order_id(m.group(1))
    return None


def _strip_order_id(name):
    """'Chennapatnam Tiffins ORDER ID: 1234' -> 'Chennapatnam Tiffins'."""
    return re.split(r"\s*\border\s*id\b", name, flags=re.IGNORECASE)[0].strip(" .,:-") or None


def parse_swiggy_items(body_text):
    """Parse item names and prices from Swiggy/Instamart order email body_text."""
    if not body_text:
        return []
    items = []
    # Pattern: "1 x Item Name ₹29.00" or "1 x Item Name Rs.29"
    item_pattern = r'(\d+)\s*x\s+(.+?)\s+(?:₹|Rs\.?)\s*([\d,]+(?:\.\d{2})?)'
    for m in re.finditer(item_pattern, body_text):
        qty = int(m.group(1))
        name = m.group(2).strip()
        price = float(m.group(3).replace(",", ""))
        items.append({"name": name, "qty": qty, "price": price})
    return items


def parse_swiggy_order_type(subject, body_text):
    """Determine if order is food delivery, Instamart, or Dineout."""
    text = ((subject or "") + " " + (body_text or "")).lower()
    if "instamart" in text:
        return "instamart"
    elif "dineout" in text:
        return "dineout"
    return "food_delivery"


def parse_amazon_subject(subject):
    """Extract product names from Amazon email subjects."""
    if not subject:
        return [], 0
    products = []
    # Handle both ASCII quotes and Unicode curly quotes (\u201c \u201d)
    # Pattern: Delivered: "Product Name..." or Shipped: "Product Name..."
    pattern = r'(?:Delivered|Shipped|Out for delivery|Ordered):\s*[\u201c""]([^\u201d""]+)[\u201d""]'
    for m in re.finditer(pattern, subject):
        name = m.group(1).strip()
        if name.endswith("..."):
            name = name[:-3].strip()
        if name:
            products.append(name)
    # Also check for "and N more item(s)"
    more = re.search(r'and\s+(\d+)\s+more\s+item', subject)
    extra_count = int(more.group(1)) if more else 0
    return products, extra_count


def _clean_product_name(name):
    """Clean up a product name extracted from HTML."""
    if not name:
        return None
    # Decode HTML entities
    name = re.sub(r'&amp;', '&', name)
    name = re.sub(r'&lt;', '<', name)
    name = re.sub(r'&gt;', '>', name)
    name = re.sub(r'&quot;', '"', name)
    name = re.sub(r'&#\d+;', '', name)
    name = re.sub(r'&\w+;', '', name)
    # Strip whitespace and newlines
    name = re.sub(r'\s+', ' ', name).strip()
    # Skip generic/navigation text
    skip_phrases = [
        'view order', 'track package', 'return items', 'cancel items',
        'customer service', 'your account', 'sign in', 'amazon.in',
        'click here', 'unsubscribe', 'privacy', 'conditions of use',
        'delivery details', 'arriving', 'order details', 'need help',
        'buy again', 'write a review', 'rate your',
        'hello', 'hi ', 'dear ',          # the greeting line ("Hello <name>,") is not a product
    ]
    lower = name.lower()
    if len(name) < 5:
        return None
    for phrase in skip_phrases:
        if lower == phrase or lower.startswith(phrase):
            return None
    return name


def parse_amazon_html_body(html):
    """
    Parse Amazon order/delivery email HTML to extract product details.
    Returns a dict with "products" list, "order_id", and "order_total".

    Amazon India emails use table-based layouts with product names in links
    or bold text, prices in adjacent cells.
    """
    if not html:
        return {"products": [], "order_id": None, "order_total": None}

    products = []
    order_id = None
    order_total = None

    # --- Extract Order ID ---
    oid_patterns = [
        r'[Oo]rder\s*#?\s*([\d]{3}-[\d]{7}-[\d]{7})',
        r'[Oo]rder\s*(?:number|no\.?|ID)\s*[:#]?\s*([\d]{3}-[\d]{7}-[\d]{7})',
    ]
    for p in oid_patterns:
        m = re.search(p, html)
        if m:
            order_id = m.group(1)
            break

    # --- Extract Order Total ---
    total_patterns = [
        r'(?:Order\s*Total|Grand\s*Total|Total\s*for\s*this\s*Order)[:\s]*(?:<[^>]*>)*\s*(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{2})?)',
        r'(?:Order\s*Total|Grand\s*Total)[^<]*(?:<[^>]*>)*[^₹R]*(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{2})?)',
    ]
    for p in total_patterns:
        m = re.search(p, html, re.IGNORECASE | re.DOTALL)
        if m:
            try:
                order_total = float(m.group(1).replace(",", ""))
            except Exception:
                pass
            break

    # --- Extract Products ---
    # Strategy 1: Amazon product links with /dp/ or /gp/product
    product_link_pattern = r'<a[^>]+href="[^"]*(?:/dp/|/gp/product/)[^"]*"[^>]*>([^<]+)</a>'
    link_products = re.findall(product_link_pattern, html, re.IGNORECASE)

    seen_names = set()
    for name in link_products:
        clean = _clean_product_name(name)
        if clean and clean.lower() not in seen_names:
            seen_names.add(clean.lower())
            products.append({"name": clean, "qty": 1, "price": None})

    # Try to find prices near product names
    for product in products:
        escaped_name = re.escape(product["name"][:30])
        price_near = re.search(
            escaped_name + r'.*?(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{2})?)',
            html, re.IGNORECASE | re.DOTALL
        )
        if price_near:
            try:
                product["price"] = float(price_near.group(1).replace(",", ""))
            except Exception:
                pass

    # Strategy 2: If no products from links, try bold text with decent length
    if not products:
        bold_pattern = r'<(?:b|strong)[^>]*>([^<]{10,150})</(?:b|strong)>'
        bold_matches = re.findall(bold_pattern, html, re.IGNORECASE)
        for name in bold_matches:
            clean = _clean_product_name(name)
            if clean and clean.lower() not in seen_names and len(clean) > 15:
                seen_names.add(clean.lower())
                products.append({"name": clean, "qty": 1, "price": None})

    # Strategy 3: Look for td-based item rows with prices
    if not products:
        td_item_pattern = (
            r'<td[^>]*>([^<]{10,200})</td>'
            r'[^<]*(?:<[^>]*>[^<]*)*?'
            r'<td[^>]*>\s*(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{2})?)\s*</td>'
        )
        for m in re.finditer(td_item_pattern, html, re.IGNORECASE | re.DOTALL):
            name = _clean_product_name(m.group(1))
            if name and len(name) > 10:
                try:
                    price = float(m.group(2).replace(",", ""))
                except Exception:
                    price = None
                products.append({"name": name, "qty": 1, "price": price})

    # Try to extract qty for found products
    for product in products:
        escaped = re.escape(product["name"][:30])
        qty_match = re.search(
            escaped + r'.*?(?:Qty|Quantity)\s*[:\s]*(\d+)',
            html, re.IGNORECASE | re.DOTALL
        )
        if qty_match:
            product["qty"] = int(qty_match.group(1))

    return {
        "products": products,
        "order_id": order_id,
        "order_total": order_total,
    }


def parse_generic_subject(subject, brand):
    """Extract useful details from email subjects for any merchant."""
    if not subject:
        return {}
    details = {}

    # Order ID patterns
    order_patterns = [
        r'order\s*(?:id|#|no\.?)\s*[:\s]*(\S+)',
        r'#(\d{5,})',
    ]
    for p in order_patterns:
        m = re.search(p, subject, re.IGNORECASE)
        if m:
            details["order_id"] = m.group(1)
            break

    # Status patterns
    status_keywords = ["delivered", "shipped", "confirmed", "placed", "cancelled", "refunded", "out for delivery"]
    for kw in status_keywords:
        if kw in subject.lower():
            details["status"] = kw
            break

    # Amount from subject
    amount = extract_amount_from_text(subject)
    if amount:
        details["amount"] = amount

    return details


# iter-18 M12. Enrichment used to link an order email to ANY debit within ±5% and ±1 day and then
# overwrite its merchant and category — so a Rs 15,000 CRED bill payment (Finance, excluded from
# spend) next to a Rs 14,500 Amazon order became "Shopping" spend. The rules now:
#   * the amount must match to the rupee (AMOUNT_TOLERANCE_RS) and the date within ±1 IST day;
#   * more than one candidate is ambiguous -> link nothing (the email is retried every hour for RETRY_DAYS);
#   * rows that cannot be a brand purchase are never candidates (PROTECTED_*);
#   * rows the owner categorised by hand are linked but never relabelled.
AMOUNT_TOLERANCE_RS = 1.0
DATE_WINDOW_DAYS = 1
PROTECTED_CATEGORIES = ("Finance", "Self Transfer")
# Rows that are not a brand purchase: bill payments, money in, and the charge lines a statement adds
# (an IGST-on-fee line of Rs 90 was relabelled "Swiggy - Charcoal BBQ", iter-18 verifier).
PROTECTED_KINDS = ("bill_payment", "transfer_in", "fee", "interest", "emi_principal", "emi_conversion", "atm")
# Promotions are not orders ("₹19 deals", "₹3/trip", "Flat 60% off"): their figures are prices of
# offers, and a Rs 2-20 UPI payment the same day is not the promoted purchase (iter-18 verifier).
_PROMO_SUBJECT = re.compile(
    r"\bdeals?\b|\boffers?\b|%\s*off\b|\bflat\s+\d|\bsave\b|\bdiscount|\bcoupon|\bcashback\b|/\s*trip\b|"
    r"\bper\s+trip\b|\bunlock|\bfree\s+delivery\b|\bsale\b|\bwin\b|\breward", re.I)
# Below this, an email/transaction amount match is too likely to be a coincidence to relabel by.
MIN_MATCH_AMOUNT_RS = 30.0
# An Uber trip paid in cash is paid to the driver — often by UPI to a person's name, which says
# nothing about Uber. Such a payment is only the trip's when it was made around the receipt: from
# 2 minutes before (clock skew) to 2 hours after it. Same-amount payments to people a day before or
# after a trip were labelled Uber (iter-18 verifier: Rs 330, Rs 700, Rs 180).
UBER_PERSON_PAYMENT_WINDOW_S = (-120, 2 * 3600)

def is_promotional(subject):
    return bool(_PROMO_SUBJECT.search(subject or ""))


# An order / ride / booking / payment confirmation — the only emails whose amount is a purchase.
# Everything a brand sends besides these (marketing "Your breakfast deserves better ... starting at
# just Rs 39", surveys, "Payment Failed", refunds, Splitwise "X paid you") used to be matched to
# any debit of the same amount (iter-18 verifier: Airtel Rs 39 -> Swiggy, a Swiggy Rs 716 debit ->
# an Amazon refund). The subject lines below are the transactional ones on file.
_ORDER_SUBJECT = re.compile(
    r"\border\b[^|]*\b(?:delivered|placed|confirmed)\b"       # Swiggy / Instamart / Toing "order was delivered"
    r"|\border\s+from\b"                                       # "Your Zomato order from <restaurant>"
    r"|\btrip\s+with\b|\bride\s+(?:receipt|with|details)\b"     # "Your Saturday evening trip with Uber"
    r"|^\s*(?:ordered|delivered|shipped)\s*:"                  # Amazon "Ordered: ...", "Delivered: 1 item | Order #"
    r"|\border\s*(?:#|id\b|no\b|number\b)"
    r"|\bpayment\s+(?:was\s+)?successful\b|\bpaid\s+successfully\b"   # "Your Swiggy Dineout payment was successful"
    r"|\bbooking\s+(?:id|confirmed|confirmation)\b|\bredbus\s+ticket\b|\byour\s+tickets?\b[^|]*\bare\s+here\b"
    r"|\b(?:membership|gold)\b[^|]*\b(?:renewed|activated)\b"
    r"|\binvoice\b|\breceipt\b", re.I)
_NOT_ORDER_SUBJECT = re.compile(
    r"refund|cancel|fail|declin|reversed|\breturn|survey|feedback|rate\s+your|how\s+was|hear\s+(?:from\s+)?you"
    r"|help\s+(?:us|make)|one[-\s]time\s+password|\botp\b|paid\s+you|balance\s+for|log\s?in|privacy|passkey", re.I)


def is_order_email(subject):
    """Is this email a purchase confirmation whose amount may be matched to a debit?"""
    s = subject or ""
    return bool(_ORDER_SUBJECT.search(s)) and not _NOT_ORDER_SUBJECT.search(s) and not is_promotional(s)


# The brands' own names, so an Amazon email is never matched to a debit that names Swiggy
# ("RAZ*SwiggyBangalore" Rs 716 vs an Amazon email for Rs 717) and an Uber receipt never to Blinkit.
_BRAND_WORDS = {
    "Swiggy": ("swiggy", "instamart", "dineout"),
    "Swiggy Instamart": ("swiggy", "instamart"),
    "Toing": ("toing",),
    "Zomato": ("zomato",),
    "Blinkit": ("blinkit",),
    "Zepto": ("zepto",),
    "Uber": ("uber",),
    "Ola": ("ola",),
    "Rapido": ("rapido",),
    "Amazon": ("amazon", "amzn"),
    "Flipkart": ("flipkart",),
    "BookMyShow": ("bookmyshow",),
    "RedBus": ("redbus",),
    "Splitwise": ("splitwise",),
}
_ALL_BRAND_WORDS = {w for ws in _BRAND_WORDS.values() for w in ws} | {"bigbasket", "myntra", "ajio", "dunzo"}


def _says(word, text):
    # descriptors run words together ("RAZ*SwiggyBangalore"), so long names match anywhere; short
    # ones only at a word start ("ola" is not "cola", "uber" is not "tuber")
    return word in text if len(word) >= 5 else re.search(r"(?<![a-z])" + word, text) is not None


def names_other_brand(text, brand):
    """Does a transaction's text name a brand other than the email's (and not the email's own)?"""
    t = (text or "").lower()
    own = set(_BRAND_WORDS.get(brand, ()))
    if any(_says(w, t) for w in own):
        return False
    return any(_says(w, t) for w in _ALL_BRAND_WORDS - own)


def email_brand(sender_email, subject, body=""):
    """(brand, enrichment_type, category) for a brand email, or None. Instamart orders are sent from
    swiggy.in and say so in the subject; they are groceries, not food (iter-18 verifier: ~20
    'Payu*Instamart' Groceries rows were re-filed as Swiggy / Food & Dining)."""
    s = (sender_email or "").lower()
    for sender_pattern, brand, enrichment_type, category in BRAND_EMAILS:
        if sender_pattern in s:
            if brand == "Swiggy" and "instamart" in f"{subject or ''}".lower():
                return ("Swiggy Instamart", "grocery_order", "Groceries")
            return (brand, enrichment_type, category)
    return None


def has_manual_override(conn, txn_id, confidence):
    if (confidence or "") == "manual":
        return True
    return conn.execute(
        "SELECT 1 FROM category_overrides WHERE transaction_id = ? LIMIT 1", (txn_id,)
    ).fetchone() is not None


def _email_time(date_header):
    """When an email was sent, as an aware UTC datetime, or None."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(date_header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return parse_stored_timestamp(date_header)


def _txn_time(conn, row):
    """When a transaction happened, as far as its own source says (aware UTC), or None."""
    when = parse_stored_timestamp(row["occurred_at"])
    if when is not None:
        return when
    if row["source"] == "sms" and row["source_ref"]:
        r = conn.execute("SELECT phone_timestamp, received_at FROM sms WHERE id = ?", (row["source_ref"],)).fetchone()
        if r:
            return parse_stored_timestamp(r["phone_timestamp"]) or parse_stored_timestamp(r["received_at"])
    if row["source"] == "email" and row["source_ref"]:
        r = conn.execute("SELECT date FROM emails WHERE id = ?", (row["source_ref"],)).fetchone()
        if r:
            return _email_time(r["date"])
    return None


def _uber_payment_fits(conn, row, email_time):
    """An Uber receipt matches a debit that names Uber by date; a payment to anyone else (the
    driver) only when it was made within UBER_PERSON_PAYMENT_WINDOW_S of the receipt."""
    if _says("uber", (row["raw_description"] or "").lower()):
        return True
    when = _txn_time(conn, row)
    if when is None or email_time is None:
        return False
    lo, hi = UBER_PERSON_PAYMENT_WINDOW_S
    return lo <= (when - email_time).total_seconds() <= hi


def match_transaction(conn, amount, email_date, brand=None, email_time=None, email_id=None):
    """The single debit an order email of `amount` on `email_date` (IST, YYYY-MM-DD) can belong to,
    or None when there is no candidate or more than one.

    Not candidates: rows that cannot be a brand purchase (PROTECTED_*), estimated foreign amounts,
    a debit whose text names another brand, and a debit another email already explains (two Rs 262
    orders from one restaurant on one day were both linked to a single debit)."""
    try:
        day = datetime.strptime(email_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    lo = (day - timedelta(days=DATE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    hi = (day + timedelta(days=DATE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    cat_marks = ",".join("?" * len(PROTECTED_CATEGORIES))
    kind_marks = ",".join("?" * len(PROTECTED_KINDS))
    rows = conn.execute(
        "SELECT id, confidence, raw_description, merchant, source, source_ref, occurred_at FROM transactions "
        "WHERE ABS(amount - ?) <= ? AND date BETWEEN ? AND ? "
        "AND is_duplicate = 0 AND txn_type = 'debit' "
        "AND COALESCE(fx_estimated, 0) = 0 "          # an estimated INR amount is not an amount to match
        f"AND COALESCE(category, '') NOT IN ({cat_marks}) "
        f"AND COALESCE(kind, '') NOT IN ({kind_marks}) "
        "AND id NOT IN (SELECT transaction_id FROM email_enrichments "
        "               WHERE transaction_id IS NOT NULL AND email_id IS NOT ?)",
        (amount, AMOUNT_TOLERANCE_RS, lo, hi, *PROTECTED_CATEGORIES, *PROTECTED_KINDS, email_id),
    ).fetchall()
    if brand:
        rows = [r for r in rows if not names_other_brand(r["raw_description"], brand)]
    if brand == "Uber":
        rows = [r for r in rows if _uber_payment_fits(conn, r, email_time)]
    return rows[0] if len(rows) == 1 else None


def _email_details(email, brand):
    """(amount, extracted): the amount an order email states and the order detail it carries."""
    body = (email["body_text"] or email["body_snippet"] or "")
    subject = email["subject"] or ""
    body_html = email["body_html"] or ""

    amount = extract_amount_from_text(subject + " " + body)
    extracted = {"brand": brand, "email_subject": subject}

    if brand == "Swiggy":
        restaurant = extract_restaurant_from_swiggy(subject, body)
        if restaurant:
            extracted["restaurant"] = restaurant
        items = parse_swiggy_items(body)
        if items:
            extracted["items"] = items
            extracted["item_count"] = len(items)
        extracted["order_type"] = parse_swiggy_order_type(subject, body)

    elif brand == "Swiggy Instamart":
        items = parse_swiggy_items(body)
        if items:
            extracted["items"] = items
            extracted["item_count"] = len(items)
        extracted["order_type"] = "instamart"

    elif brand == "Zomato":
        restaurant = extract_restaurant_from_zomato(subject, body)
        if restaurant:
            extracted["restaurant"] = restaurant

    elif brand == "Amazon":
        # Try HTML body first for rich product data
        if body_html:
            html_data = parse_amazon_html_body(body_html)
            if html_data["products"]:
                extracted["products"] = html_data["products"]
            if html_data["order_id"]:
                extracted["order_id"] = html_data["order_id"]
            if html_data["order_total"]:
                extracted["order_total"] = html_data["order_total"]
                # Use HTML-extracted total as amount if we didn't find one
                if not amount:
                    amount = html_data["order_total"]

        # Fall back to subject parsing if no products from HTML
        if "products" not in extracted or not extracted["products"]:
            products, extra = parse_amazon_subject(subject)
            if products:
                extracted["products"] = [{"name": p, "qty": 1, "price": None} for p in products]
                if extra:
                    extracted["extra_items"] = extra
        extracted.update(parse_generic_subject(subject, brand))

    else:
        # Generic enrichment for all other brands
        extracted.update(parse_generic_subject(subject, brand))
    return amount, extracted


def _merchant_label(brand, extracted):
    """The merchant string a matched order gives its transaction (HTML entities decoded:
    "Domino&#39;s" -> "Domino's")."""
    label = brand
    if "restaurant" in extracted:
        label = f"{brand} - {extracted['restaurant']}"
    elif brand == "Amazon" and extracted.get("products"):
        names = [p["name"] for p in extracted["products"] if isinstance(p, dict) and p.get("name")]
        if len(names) == 1:
            label = f"Amazon - {names[0][:60]}"
        elif len(names) > 1:
            label = f"Amazon - {names[0][:40]} +{len(names)-1} more"
    return html.unescape(label)


def _label(brand, extracted):
    """Merchant label for a matched order: restaurant / product, else the first items (Instamart),
    else the brand — the same string the hourly relink pass would settle on, so nothing churns."""
    label = _merchant_label(brand, extracted)
    if label == brand:
        from modules.email.relink import _build_enriched_merchant
        label = _build_enriched_merchant(brand, extracted) or brand
    return label


def _link(conn, txn, brand, category, extracted):
    """Relabel the transaction an order email explains, unless the owner labelled it by hand."""
    if not has_manual_override(conn, txn["id"], txn["confidence"]):
        conn.execute(
            "UPDATE transactions SET merchant = ?, category = ?, confidence = 'enriched' WHERE id = ?",
            (_label(brand, extracted), category, txn["id"]),
        )


def _by_time(rows):
    far_past = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return sorted(rows, key=lambda r: (_email_time(r["date"]) or far_past, r["id"]))


def _file_email(conn, email):
    """File a brand email that has no enrichment row yet (not matched here)."""
    found = email_brand(email["sender_email"], email["subject"])
    if not found or not _parse_email_date(email["date"]):
        return
    brand, enrichment_type, _category = found
    _amount, extracted = _email_details(email, brand)
    conn.execute(
        "INSERT INTO email_enrichments (email_id, transaction_id, enrichment_type, brand, extracted_data) "
        "VALUES (?, NULL, ?, ?, ?)",
        (email["id"], enrichment_type, brand, json.dumps(extracted)),
    )


_FILED = ("SELECT ee.id AS ee_id, e.id, e.subject, e.body_snippet, e.body_text, e.body_html, e.date, e.sender_email "
          "FROM email_enrichments ee JOIN emails e ON e.id = ee.email_id")


def _match_filed(conn, rows, before_link=None):
    """Match filed emails to their debits, oldest sent first, and link them. Returns {email_id: txn_id}.
    `before_link(txn_id)` runs just before a transaction is relabelled (the rebuild records labels)."""
    linked = {}
    for email in _by_time(rows):
        found = email_brand(email["sender_email"], email["subject"])
        email_date = _parse_email_date(email["date"])
        if not found or not email_date:
            continue
        brand, enrichment_type, category = found
        amount, extracted = _email_details(email, brand)
        conn.execute("UPDATE email_enrichments SET brand = ?, enrichment_type = ?, extracted_data = ? WHERE id = ?",
                     (brand, enrichment_type, json.dumps(extracted), email["ee_id"]))
        if not (amount and amount >= MIN_MATCH_AMOUNT_RS and is_order_email(email["subject"])):
            continue
        txn = match_transaction(conn, amount, email_date, brand, _email_time(email["date"]), email["id"])
        if txn is None:
            continue
        conn.execute("UPDATE email_enrichments SET transaction_id = ? WHERE id = ?", (txn["id"], email["ee_id"]))
        if before_link:
            before_link(txn["id"])
        _link(conn, txn, brand, category, extracted)
        linked[email["id"]] = txn["id"]
    return linked


def _unenriched_brand_emails(conn):
    rows = []
    for sender_pattern, _brand, _type, _category in BRAND_EMAILS:
        rows += conn.execute(
            "SELECT e.id, e.subject, e.body_snippet, e.body_text, e.body_html, e.date, e.sender_email "
            "FROM emails e WHERE e.sender_email LIKE ? "
            "AND NOT EXISTS (SELECT 1 FROM email_enrichments ee WHERE ee.email_id = e.id)",
            (f"%{sender_pattern}%",)).fetchall()
    unique = {r["id"]: r for r in rows}          # an address can match two patterns
    return _by_time(unique.values())


# An order email whose debit has not arrived yet (statement line weeks later, an SMS recovered by the
# backfill) is tried again every hour for this long.
RETRY_DAYS = 400       # the March 2026 bulk import brought debits 62-172 days after their emails


def run_enrichment(conn=None):
    """Hourly: file every new brand email (one enrichment row per email, whatever brand it is filed
    under), then match every unlinked order email sent in the last RETRY_DAYS to its debit — oldest
    first, so of two same-amount orders the first one placed explains the payment, and an order whose
    debit arrives later is still linked. Promotions, surveys, refunds, failed payments and tiny
    amounts never link."""
    conn = conn or get_db()
    new_ids = set()
    for email in _unenriched_brand_emails(conn):
        try:
            _file_email(conn, email)
            new_ids.add(email["id"])
        except Exception:
            pass
    since = datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)
    open_rows = [r for r in conn.execute(_FILED + " WHERE ee.transaction_id IS NULL").fetchall()
                 if is_order_email(r["subject"])
                 and (r["id"] in new_ids or (_email_time(r["date"]) or since) >= since)]
    linked = _match_filed(conn, open_rows)
    conn.commit()
    return {"enriched": len(linked)}


def rebuild_enrichment_links(conn=None, dry_run=True):
    """Re-make every email -> transaction link under today's rules (once, at deploy).

    The old matcher left links today's rules would not make (iter-18 verifier: 173 live 'enriched'
    rows — Blinkit purchases labelled 'Swiggy - <restaurant>', an Airtel Rs 39 payment labelled
    Swiggy from a marketing email, IGST fee lines labelled with a restaurant, two orders on one
    debit, links left on rows that were later merged into a statement line). Patching link by link
    kept ambiguous links and never re-matched the emails it unlinked, so every link is re-made:

      1. a row whose labels came from an email ('enriched') gets the categoriser's labels back,
         unless the owner labelled it by hand;
      2. every brand email — already filed or not — is filed under its brand (Instamart orders from
         swiggy.in included) and matched again, oldest first, exactly as the hourly pass would.

    Runs in one savepoint: dry_run rolls everything back and only reports. Idempotent."""
    from modules.categorizer import categorize, card_numbers, context_for_row
    conn = conn or get_db()
    before_links = {r["email_id"]: r["transaction_id"] for r in conn.execute(
        "SELECT email_id, transaction_id FROM email_enrichments WHERE transaction_id IS NOT NULL")}
    touched = {r["id"]: (r["merchant"], r["category"]) for r in conn.execute(
        "SELECT t.id, t.merchant, t.category FROM transactions t "
        "WHERE t.id IN (SELECT transaction_id FROM email_enrichments WHERE transaction_id IS NOT NULL)")}

    conn.execute("SAVEPOINT enrichment_rebuild")
    try:
        cards = card_numbers(conn)
        for t in conn.execute(
                "SELECT id, source, card_last4, raw_text, raw_description, confidence FROM transactions "
                "WHERE confidence = 'enriched' "
                "AND id IN (SELECT transaction_id FROM email_enrichments WHERE transaction_id IS NOT NULL)").fetchall():
            if has_manual_override(conn, t["id"], t["confidence"]):
                continue
            ctx = context_for_row(t["source"], t["card_last4"], t["raw_text"] or t["raw_description"], cards)
            merchant, category, confidence = categorize(t["raw_description"], context=ctx)
            conn.execute("UPDATE transactions SET merchant = ?, category = ?, confidence = ? WHERE id = ?",
                         (merchant, category, confidence, t["id"]))
        conn.execute("UPDATE email_enrichments SET transaction_id = NULL WHERE transaction_id IS NOT NULL")

        # every brand email — filed before or synced after the last hourly pass — is matched again,
        # oldest first, exactly as the hourly pass matches (without its retry window)
        for email in _unenriched_brand_emails(conn):
            _file_email(conn, email)

        def remember(tid):
            if tid not in touched:              # not linked before: its labels are the original ones
                cur = conn.execute("SELECT merchant, category FROM transactions WHERE id = ?", (tid,)).fetchone()
                touched[tid] = (cur["merchant"], cur["category"])
        after_links = _match_filed(conn, conn.execute(_FILED).fetchall(), before_link=remember)

        relabelled = []
        for tid, (merchant, category) in touched.items():
            now = conn.execute("SELECT merchant, category FROM transactions WHERE id = ?", (tid,)).fetchone()
            if now and (now["merchant"], now["category"]) != (merchant, category):
                relabelled.append({"txn": tid, "from": [merchant, category], "to": [now["merchant"], now["category"]]})
    except Exception:
        conn.execute("ROLLBACK TO enrichment_rebuild")
        conn.execute("RELEASE enrichment_rebuild")
        raise
    if dry_run:
        conn.execute("ROLLBACK TO enrichment_rebuild")
    conn.execute("RELEASE enrichment_rebuild")

    kept = sum(1 for e, t in after_links.items() if before_links.get(e) == t)
    return {
        "dry_run": dry_run,
        "links_before": len(before_links),
        "links_after": len(after_links),
        "kept": kept,
        "moved": sum(1 for e, t in after_links.items() if e in before_links and before_links[e] != t),
        "new": sum(1 for e in after_links if e not in before_links),
        "dropped": sum(1 for e in before_links if e not in after_links),
        "relabelled": len(relabelled),
        "samples": relabelled[:40],
    }
