"""
Foreign-currency estimates for SMS spends — iter-18 M7.

A bank SMS for a foreign-currency card spend states the amount in that currency ("USD118.00"); the
INR the card is actually billed only appears weeks later on the statement. Before iter-18 the SMS
amount was stored as if it were rupees (USD 20 recorded as Rs 20). Now the row keeps
`currency` + `original_amount` and `amount` holds an INR ESTIMATE flagged `fx_estimated = 1`;
modules.merge pairs it with the statement line within FX_TOLERANCE_PCT and the statement's exact
INR wins.

These rates are ESTIMATES, not market data, and are deliberately "card-billed" rates: the base rate
plus the ~3.5% foreign-currency markup and GST on it that Indian card issuers add. Calibrated against
the owner's own statements: USD 118.00 billed Rs 10,916.12 (Mar 2026) and Rs 11,357.51 (Jul 2026),
i.e. 92.5-96.3 INR per USD. Revisit when the rupee moves more than a few percent — an estimate more
than FX_TOLERANCE_PCT away from the billed amount stops the SMS row merging with its statement line.
"""

# INR per one unit of the currency, card-billed estimate (see module docstring). Last reviewed 2026-10.
APPROX_INR_PER_UNIT = {
    "USD": 95.0,
    "EUR": 110.0,
    "GBP": 126.0,
    "AED": 25.9,
    "SGD": 73.0,
}


def normalize_currency(code):
    """'usd' / ' USD ' / None -> 'USD' / 'INR'. Rupee spellings map to INR."""
    c = (code or "").strip().upper().rstrip(".")
    if c in ("", "RS", "INR", "₹"):
        return "INR"
    return c


def to_inr_estimate(currency, amount):
    """Estimated INR value of `amount` in `currency`, rounded to paise.

    INR passes through unchanged. Returns None for a currency we have no rate for, so the caller
    can refuse to invent a rupee figure (it must never store a foreign amount as if it were INR)."""
    if amount is None:
        return None
    cur = normalize_currency(currency)
    if cur == "INR":
        return round(float(amount), 2)
    rate = APPROX_INR_PER_UNIT.get(cur)
    if rate is None:
        return None
    return round(float(amount) * rate, 2)
