"""
Two-pass merchant categorizer:
1. Alias table lookup (legal entity -> brand + category)
2. Keyword fallback
3. Heuristic: P2P UPI detection, address-based rent detection

iter-18 M14: short words match as whole words (see _GUARDED), card spends never take a
Transfer alias, and recategorize_with_context() re-runs the categorizer over history with the
same source context a new row gets from txn_store.insert_transaction().
"""
import re
from collections import Counter, defaultdict
from functools import lru_cache

from db import get_db

SEED_ALIASES = [
    # -- Food & Dining --
    ("bundl technologies", "contains", "Swiggy", "Food & Dining", "Swiggy legal entity"),
    ("bundl tech", "contains", "Swiggy", "Food & Dining", None),
    ("swiggy", "contains", "Swiggy", "Food & Dining", None),
    ("zomato", "contains", "Zomato", "Food & Dining", None),
    ("chaayos", "contains", "Chaayos", "Food & Dining", None),
    ("cafe coffee day", "contains", "CCD", "Food & Dining", None),
    ("starbucks", "contains", "Starbucks", "Food & Dining", None),
    ("dominos", "contains", "Dominos", "Food & Dining", None),
    ("mcdonalds", "contains", "McDonalds", "Food & Dining", None),
    ("mcdonald", "contains", "McDonalds", "Food & Dining", None),
    ("kfc", "contains", "KFC", "Food & Dining", None),
    ("subway", "contains", "Subway", "Food & Dining", None),
    ("haldiram", "contains", "Haldirams", "Food & Dining", None),
    ("barbeque nation", "contains", "Barbeque Nation", "Food & Dining", None),
    ("bbq nation", "contains", "Barbeque Nation", "Food & Dining", None),
    ("burger king", "contains", "Burger King", "Food & Dining", None),
    ("pizza hut", "contains", "Pizza Hut", "Food & Dining", None),
    ("little goa", "contains", "Little Goa", "Food & Dining", None),
    ("fresh samosa", "contains", "Fresh Samosa", "Food & Dining", None),
    ("sri annapurna caterers", "contains", "Sri Annapurna Caterers", "Food & Dining", None),
    ("annapurna caterer", "contains", "Sri Annapurna Caterers", "Food & Dining", None),
    ("sneha fresh chicken", "contains", "Sneha Fresh Chicken", "Food & Dining", None),
    ("pluxee debit", "contains", "Pluxee Meal Card", "Food & Dining", "Meal card"),
    ("pluxee", "contains", "Pluxee", "Food & Dining", "Meal/gift card"),
    ("sodexo", "contains", "Sodexo", "Food & Dining", "Meal card"),
    ("delightful gourmet", "contains", "Delightful Gourmet", "Food & Dining", None),
    ("amul", "contains", "Amul", "Food & Dining", None),
    # HDFC card SMS on the Swiggy card (9434) print the merchant's registered address instead of
    # its name. These were filed as "Rent & Housing / Property address", but every one pairs with
    # a Swiggy line on the same card's statement (PYU*Swiggy, WWW SWIGGY IN, PayU*Swiggy Limited)
    # and they are ~Rs 250-600 orders several times a day (iter-18 M14).
    ("sy nos 8 1 to 55 6", "contains", "Swiggy", "Food & Dining", "Swiggy registered address in HDFC SMS"),
    ("sy no 8 14", "contains", "Swiggy", "Food & Dining", "Swiggy registered address in HDFC SMS"),
    ("no 55 sy no", "contains", "Swiggy", "Food & Dining", "Swiggy registered address in HDFC SMS"),
    ("devara", "contains", "Swiggy", "Food & Dining", "Devarabeesanahalli (Swiggy HQ), truncated in HDFC SMS"),
    # Same card, same months (Apr-May 2025, before statement PDFs were parsed), same order sizes;
    # no statement line to name the platform, but certainly food orders, not housing.
    ("vatika towers", "contains", "Vatika Towers", "Food & Dining", "Food-order merchant address on the Swiggy card"),
    ("9thfloor tower", "contains", "9th Floor Tower D", "Food & Dining", "Food-order merchant address on the Swiggy card"),

    # -- Groceries --
    ("instamart svcs", "contains", "Swiggy Instamart", "Groceries", None),
    ("instamart", "contains", "Swiggy Instamart", "Groceries", None),
    ("bigbasket", "contains", "BigBasket", "Groceries", None),
    ("big basket", "contains", "BigBasket", "Groceries", None),
    ("blinkit", "contains", "Blinkit", "Groceries", None),
    ("blink commerce", "contains", "Blinkit", "Groceries", "Blinkit legal entity"),
    ("zepto", "contains", "Zepto", "Groceries", None),
    ("dunzo", "contains", "Dunzo", "Groceries", None),
    ("grofers", "contains", "Blinkit", "Groceries", None),
    ("jiomart", "contains", "JioMart", "Groceries", None),
    ("dmart", "contains", "DMart", "Groceries", None),
    ("avenue supermar", "contains", "DMart", "Groceries", "DMart legal entity (ICICI SMS truncates it)"),
    ("ratnadeep", "contains", "Ratnadeep", "Groceries", None),
    ("ushodaya super", "contains", "Ushodaya Super Markets", "Groceries", None),
    ("vijetha super", "contains", "Vijetha Super Markets", "Groceries", None),
    ("sree vyshnavi traders", "contains", "Sree Vyshnavi Traders", "Groceries", None),
    ("subham grand", "contains", "Subham Grand", "Groceries", "Local supermarket"),
    ("more retail", "contains", "More Retail", "Groceries", None),
    ("reliance fresh", "contains", "Reliance Fresh", "Groceries", None),
    ("spencer", "contains", "Spencers", "Groceries", None),
    ("nature basket", "contains", "Natures Basket", "Groceries", None),
    ("msw*suguna", "contains", "Suguna", "Groceries", "Poultry/groceries"),

    # -- Shopping --
    ("amazon", "contains", "Amazon", "Shopping", None),
    ("flipkart", "contains", "Flipkart", "Shopping", None),
    ("myntra", "contains", "Myntra", "Shopping", None),
    ("ajio", "contains", "AJIO", "Shopping", None),
    ("nykaa", "contains", "Nykaa", "Shopping", None),
    ("meesho", "contains", "Meesho", "Shopping", None),
    ("tata digital", "contains", "Tata Neu", "Shopping", None),
    ("tatacliq", "contains", "Tata CLiQ", "Shopping", None),
    ("reliance retail", "contains", "Reliance Retail", "Shopping", None),
    ("reliance digital", "contains", "Reliance Digital", "Shopping", None),
    ("max fashion", "contains", "Max Fashion", "Shopping", None),
    ("lifestyle int", "contains", "Lifestyle", "Shopping", None),
    ("decathlon", "contains", "Decathlon", "Shopping", None),
    ("lenskart", "contains", "Lenskart", "Shopping", None),
    ("croma", "contains", "Croma", "Shopping", None),
    ("vijay sales", "contains", "Vijay Sales", "Shopping", None),
    ("ikeaindiapvtltd", "contains", "IKEA", "Shopping", "IKEA UPI VPA"),
    ("ikea", "contains", "IKEA", "Shopping", None),
    ("zara", "contains", "Zara", "Shopping", None),
    ("h&m", "contains", "H&M", "Shopping", None),
    ("wrogn", "contains", "Wrogn", "Shopping", None),
    ("suit studio", "contains", "Suit Studio", "Shopping", None),
    ("xyxxapparels", "contains", "Xyxx Apparels", "Shopping", None),
    ("modxcomputers", "contains", "ModX Computers", "Shopping", None),
    ("punaienterprises", "contains", "Punai Enterprises", "Shopping", None),
    ("punai enterprises", "contains", "Punai Enterprises", "Shopping", None),
    ("sanwaria texpro", "contains", "Sanwaria Texpro", "Shopping", "Textile business"),
    # iter-18 M14: Trent Ltd runs Westside and Zudio. Card SMS print the store name (WESTSIDE),
    # statements print "Westside VIJAYAWADA", "TRENT LIMITED"; "rent" inside TRENT used to file it
    # under Rent & Housing. (No "zudio" alias: the merge tests use a bare ZUDIO on a UPI context
    # as their example of a person-looking merchant; on card contexts it is Uncategorized.)
    ("trent", "contains", "Trent (Westside/Zudio)", "Shopping", "Westside/Zudio parent"),
    ("westside", "contains", "Westside", "Shopping", None),
    ("zudio", "contains", "Zudio", "Shopping", "Trent's value-fashion chain (iter-18 M14)"),
    ("titan company", "contains", "Titan", "Shopping", "Titan / Tanishq"),
    ("aditya birla fa", "contains", "Aditya Birla Fashion", "Shopping", "ICICI SMS truncation of Aditya Birla Fashion"),

    # -- Transport --
    ("uber", "contains", "Uber", "Transport", None),
    ("ola cabs", "contains", "Ola", "Transport", None),
    ("ola electric", "contains", "Ola Electric", "Transport", None),
    ("rapido", "contains", "Rapido", "Transport", None),
    ("rapido bike", "contains", "Rapido", "Transport", None),
    ("rrr travels", "contains", "Rapido", "Transport", "Common Rapido driver entity"),

    # -- Travel --
    ("irctc", "contains", "IRCTC", "Travel", None),
    # card SMS print IRCTC's legal name ("IndianRailwayCateringa"); without this the Food keyword
    # 'catering' (iter-203 E4, for canteens paid by UPI) filed train tickets as Food & Dining
    ("indian railway catering", "contains", "IRCTC", "Travel", "IRCTC legal name (iter-203 E4 round 2)"),
    ("makemytrip", "contains", "MakeMyTrip", "Travel", None),
    ("make my trip", "contains", "MakeMyTrip", "Travel", None),
    ("goibibo", "contains", "Goibibo", "Travel", None),
    ("cleartrip", "contains", "Cleartrip", "Travel", None),
    ("yatra", "contains", "Yatra", "Travel", None),
    ("redbus", "contains", "RedBus", "Travel", None),
    ("easemytrip", "contains", "EaseMyTrip", "Travel", None),
    ("oyo", "contains", "OYO", "Travel", None),
    ("indigo", "contains", "IndiGo", "Travel", None),
    ("spicejet", "contains", "SpiceJet", "Travel", None),
    ("vistara", "contains", "Vistara", "Travel", None),
    ("air india", "contains", "Air India", "Travel", None),
    ("airindia", "contains", "Air India", "Travel", None),
    ("encalm lounge", "contains", "Encalm Lounge", "Travel", "Airport lounge"),
    ("airport lounge", "contains", "Airport Lounge", "Travel", None),

    # -- Fuel --
    ("bpcl", "contains", "BPCL", "Fuel", None),
    ("hpcl", "contains", "HPCL", "Fuel", None),
    ("indian oil", "contains", "Indian Oil", "Fuel", None),
    ("iocl", "contains", "Indian Oil", "Fuel", None),
    ("bharat petroleum", "contains", "BPCL", "Fuel", None),
    ("hp petrol", "contains", "HPCL", "Fuel", None),
    ("shell", "contains", "Shell", "Fuel", None),
    ("fastag", "contains", "FASTag Toll", "Fuel", None),

    # -- Entertainment (movies, events, live experiences — NOT streaming) --
    ("pvr", "contains", "PVR", "Entertainment", None),
    ("inox", "contains", "INOX", "Entertainment", None),
    ("bookmyshow", "contains", "BookMyShow", "Entertainment", None),
    ("book my show", "contains", "BookMyShow", "Entertainment", None),
    ("bigtree enterta", "contains", "BookMyShow", "Entertainment", "BookMyShow legal entity"),

    # -- Subscriptions (digital streaming + SaaS + recurring digital services) --
    ("netflix", "contains", "Netflix", "Subscriptions", "Streaming service"),
    ("hotstar", "contains", "Hotstar", "Subscriptions", "Streaming service"),
    ("spotify", "contains", "Spotify", "Subscriptions", "Streaming service"),
    ("youtube", "contains", "YouTube Premium", "Subscriptions", "Streaming service"),
    ("prime video", "contains", "Prime Video", "Subscriptions", "Streaming service"),
    ("jiocinema", "contains", "JioCinema", "Subscriptions", "Streaming service"),
    ("zee5", "contains", "ZEE5", "Subscriptions", "Streaming service"),
    ("zee entertainment", "contains", "Zee Entertainment", "Subscriptions", "Streaming service"),
    ("sonyliv", "contains", "SonyLIV", "Subscriptions", "Streaming service"),
    ("viacom18", "contains", "JioCinema/Viacom18", "Subscriptions", "Streaming service"),
    ("apple com bill", "contains", "Apple Subscription", "Subscriptions", None),
    ("apple.com/bill", "contains", "Apple Subscription", "Subscriptions", None),
    ("google india digital", "contains", "Google Play", "Subscriptions", None),
    ("google play", "contains", "Google Play", "Subscriptions", None),
    ("openai", "contains", "OpenAI", "Subscriptions", None),
    ("chatgpt", "contains", "OpenAI ChatGPT", "Subscriptions", None),
    ("anthropic", "contains", "Anthropic Claude", "Subscriptions", None),
    ("claude.ai", "contains", "Anthropic Claude", "Subscriptions", None),
    ("github", "contains", "GitHub", "Subscriptions", None),
    ("notion", "contains", "Notion", "Subscriptions", None),
    ("figma", "contains", "Figma", "Subscriptions", None),
    ("cursor", "contains", "Cursor IDE", "Subscriptions", None),
    ("vercel", "contains", "Vercel", "Subscriptions", None),
    ("digitalocean", "contains", "DigitalOcean", "Subscriptions", None),
    ("cloudflare", "contains", "Cloudflare", "Subscriptions", None),
    ("microsoft", "contains", "Microsoft", "Subscriptions", None),
    ("porkbun", "contains", "Porkbun", "Subscriptions", "Domain registrar"),
    ("proton ag", "contains", "Proton", "Subscriptions", "Proton Mail/VPN"),
    ("entomojocom", "contains", "Entomojo", "Subscriptions", None),
    # iter-18 M14: recurring SBI 2111 subscriptions that sat in Uncategorized (statement lines) or
    # ATM Withdrawal (e-mandate SMS: "...registered by you at merchant" squeezed to "atmerchant").
    ("linkedin", "contains", "LinkedIn", "Subscriptions", "Monthly, SBI e-mandate"),
    ("canva", "contains", "Canva", "Subscriptions", "Monthly, SBI e-mandate (CANVA*, CANVAI0...)"),
    ("trendlyne", "contains", "Trendlyne", "Subscriptions", "SBI e-mandate (RAZTrendlyne)"),
    ("giskard datatech", "contains", "Trendlyne", "Subscriptions", "Trendlyne legal entity (e-mandate notices)"),
    ("adobe", "contains", "Adobe", "Subscriptions", "SBI e-mandate (ADOBESYSTEMS)"),

    # -- Bills & Utilities --
    ("airtel", "contains", "Airtel", "Bills & Utilities", None),
    ("jio platforms", "contains", "Jio", "Bills & Utilities", None),
    ("vodafone", "contains", "Vi/Vodafone", "Bills & Utilities", None),
    ("bsnl", "contains", "BSNL", "Bills & Utilities", None),
    ("tata sky", "contains", "Tata Play", "Bills & Utilities", None),
    ("bescom", "contains", "BESCOM", "Bills & Utilities", None),
    ("tsspdcl", "contains", "TSSPDCL", "Bills & Utilities", None),
    ("apspdcl", "contains", "APSPDCL", "Bills & Utilities", None),
    ("tata power", "contains", "Tata Power", "Bills & Utilities", None),
    ("adani", "contains", "Adani", "Bills & Utilities", None),
    ("mahanagar gas", "contains", "Mahanagar Gas", "Bills & Utilities", None),
    ("hathway", "contains", "Hathway", "Bills & Utilities", None),
    ("act fibernet", "contains", "ACT Fibernet", "Bills & Utilities", None),
    ("bill desk", "contains", "BillDesk", "Bills & Utilities", None),
    ("billdesk", "contains", "BillDesk", "Bills & Utilities", None),

    # -- Health --
    ("apollo", "contains", "Apollo", "Health", None),
    ("medplus", "contains", "MedPlus", "Health", None),
    ("pharmeasy", "contains", "PharmEasy", "Health", None),
    ("netmeds", "contains", "Netmeds", "Health", None),
    ("1mg", "contains", "1mg/Tata Health", "Health", None),
    ("practo", "contains", "Practo", "Health", None),
    ("healthians", "contains", "Healthians", "Health", None),
    ("thyrocare", "contains", "Thyrocare", "Health", None),
    ("lal path", "contains", "Lal PathLabs", "Health", None),
    ("cult.fit", "contains", "Cult.fit", "Health", None),
    ("cultfit", "contains", "Cult.fit", "Health", None),
    ("optival health", "contains", "Optival Health", "Health", None),

    # -- Finance --
    ("dreamplug", "contains", "CRED", "Finance", "CRED legal entity"),
    ("cred club", "contains", "CRED", "Finance", None),
    ("cred", "contains", "CRED", "Finance", "Bill payment app"),
    # Card-bill payments as they appear on statements and bank SMS. These used to reach Finance
    # only because "cred" matched inside "credit"; once that stopped (iter-18 M14) they would have
    # been read as refunds on the card and netted out of spend, so they are named explicitly.
    ("credit card payment", "contains", "Card Bill Payment", "Finance", "Statement payment line / bank debit"),
    ("credit card bill", "contains", "Card Bill Payment", "Finance", "Bank-side card bill payment"),
    ("tele transfer credit", "contains", "Card Bill Payment", "Finance", "Statement payment line"),
    ("bppy pymt", "contains", "Card Bill Payment", "Finance", "BBPS payment line (HDFC statement)"),
    ("principal credit adjustment", "contains", "Principal Adjustment (credit)", "Finance",
     "Reverses a PRINCIPAL DEBIT ADJUSTMENT EMI line; neither side is spend"),
    ("phonepe pvt", "contains", "PhonePe", "Transfer", None),
    ("phonepe merchant", "contains", "PhonePe Merchant", "Pass-Through", None),
    ("paytm", "contains", "Paytm", "Transfer", None),

    # -- EMI & Loans --
    ("hdfcbank autopay", "contains", "HDFC AutoPay", "EMI", None),
    ("sbi card emi", "contains", "SBI Card EMI", "EMI", None),
    ("bajaj finserv", "contains", "Bajaj Finserv EMI", "EMI", None),
    ("bajaj finance", "contains", "Bajaj Finance EMI", "EMI", None),
    ("interest amount amortization", "contains", "Interest Amortization", "EMI", "Loan interest"),
    ("principal amount amortization", "contains", "Principal Amortization", "EMI", "Loan principal"),
    ("principal debit adjustment", "contains", "Principal Adjustment", "EMI", "Loan adjustment"),
    # A CREDIT on the SBI 2971 statement: the card bill auto-debited from the bank (ACH). It was
    # filed as an EMI, which would make it a "refund" against EMI spend (iter-18 M14).
    ("direct debit payment", "contains", "Card Bill Auto-debit (ACH)", "Finance", "Card bill paid by ACH"),

    # -- Insurance --
    ("godigit", "contains", "Digit Insurance", "Insurance", None),
    ("acko", "contains", "Acko Insurance", "Insurance", None),
    ("max life", "contains", "Max Life Insurance", "Insurance", None),
    ("hdfc life", "contains", "HDFC Life", "Insurance", None),
    ("icici pru", "contains", "ICICI Prudential", "Insurance", None),
    ("star health", "contains", "Star Health", "Insurance", None),

    # -- Investment --
    ("zerodha", "contains", "Zerodha", "Investment", None),
    ("groww", "contains", "Groww", "Investment", None),
    ("kuvera", "contains", "Kuvera", "Investment", None),
    ("upstox", "contains", "Upstox", "Investment", None),
    ("indmoney", "contains", "INDmoney", "Investment", None),
    ("coin by zerodha", "contains", "Zerodha Coin", "Investment", None),

    # -- Rent & Housing --
    # (The Swiggy-address patterns that used to open this list now live under Food & Dining.)

    # -- Household help / home services (iter-203 E4: paid by UPI from a bank account, these were
    # filed as payments to people) --
    ("snabbit", "contains", "Snabbit", "Household help", "Home-services app (cleaning, cooking)"),
    ("mygate", "contains", "MyGate", "Rent & Housing", "Society management app (maintenance)"),
    ("story tv", "contains", "Story TV", "Subscriptions", "UPI AutoPay app subscription"),

    # -- Services --
    ("urban company", "contains", "Urban Company", "Services", None),
    ("urbanclap", "contains", "Urban Company", "Services", None),
    ("naturals salon", "contains", "Naturals Salon", "Services", None),

    # -- Pass-Through --
    ("razorpay", "contains", "Razorpay", "Pass-Through", "Payment gateway"),
    ("payu payments", "contains", "PayU", "Pass-Through", "Payment gateway"),
    ("bharatpe", "contains", "BharatPe", "Pass-Through", "Payment gateway"),

    # -- UPI Transfer VPA patterns --
    ("@ybl", "contains", "UPI Transfer", "Transfer", "PhonePe VPA"),
    ("@paytm", "contains", "UPI Transfer", "Transfer", "Paytm VPA"),
    ("@ptyes", "contains", "UPI Transfer", "Transfer", "Paytm VPA"),
    ("@pthdfc", "contains", "UPI Transfer", "Transfer", "Paytm HDFC VPA"),
    ("@ptaxis", "contains", "UPI Transfer", "Transfer", "Paytm Axis VPA"),
    ("@okaxis", "contains", "UPI Transfer", "Transfer", "GPay Axis VPA"),
    ("@oksbi", "contains", "UPI Transfer", "Transfer", "GPay SBI VPA"),
    ("@okicici", "contains", "UPI Transfer", "Transfer", "GPay ICICI VPA"),
    ("@okhdfcbank", "contains", "UPI Transfer", "Transfer", "GPay HDFC VPA"),
    ("@upi", "contains", "UPI Transfer", "Transfer", "Generic UPI VPA"),
    ("@ibl", "contains", "UPI Transfer", "Transfer", "ICICI VPA"),
    ("@icici", "contains", "UPI Transfer", "Transfer", "ICICI VPA"),
    ("@sbi", "contains", "UPI Transfer", "Transfer", "SBI VPA"),
    ("@axl", "contains", "UPI Transfer", "Transfer", "Axis VPA"),
    ("@axisbank", "contains", "UPI Transfer", "Transfer", "Axis VPA"),
    ("@apl", "contains", "UPI Transfer", "Transfer", "Amazon Pay VPA"),
    ("@hdfcbank", "contains", "UPI Transfer", "Transfer", "HDFC VPA"),
    ("@yesbank", "contains", "UPI Transfer", "Transfer", "YesBank VPA"),
    ("@kotak", "contains", "UPI Transfer", "Transfer", "Kotak VPA"),
    ("googlepay", "contains", "UPI Transfer", "Transfer", "Google Pay VPA"),

    # -- Bank Charges --
    ("annual fee", "contains", "Annual Fee", "Bank Charges", None),
    ("membership fee", "contains", "Membership Fee", "Bank Charges", None),
    ("joining fee", "contains", "Joining Fee", "Bank Charges", None),
    ("late payment", "contains", "Late Payment Fee", "Bank Charges", None),
    ("late fee", "contains", "Late Fee", "Bank Charges", None),
    ("finance charge", "contains", "Finance Charge", "Bank Charges", None),
    ("interest charge", "contains", "Interest Charge", "Bank Charges", None),
    ("overlimit", "contains", "Overlimit Fee", "Bank Charges", None),
    ("over limit", "contains", "Overlimit Fee", "Bank Charges", None),
    ("surcharge", "contains", "Surcharge", "Bank Charges", None),
    ("gst on", "contains", "GST", "Bank Charges", None),
    ("igst on", "contains", "IGST", "Bank Charges", None),
    ("cgst", "contains", "CGST", "Bank Charges", None),
    ("sgst", "contains", "SGST", "Bank Charges", None),
    ("cess on", "contains", "Cess", "Bank Charges", None),

    # -- ATM Withdrawal --
    ("atm", "contains", "ATM Withdrawal", "ATM Withdrawal", None),
    ("cash withdrawal", "contains", "Cash Withdrawal", "ATM Withdrawal", None),

    # -- Personal payee rules --
    # Add rules for the people you pay regularly (housemates, family, your own accounts), e.g.:
    #   ("jane doe", "contains", "Jane Doe", "Housemate Expenses", "Flatmate - rent split"),
    #   ("john doe", "contains", "John Doe", "Family Remittance", "Family member"),
    #   ("your own name", "contains", "Self Transfer", "Self Transfer", "Own bank transfer"),
    # Payee rules can also be set from the app (Expenses -> change category -> remember for this payee).

    # -- Family Remittance --

    # -- Home Construction --

    # -- Self Transfer --

    # -- Transfer --
    ("self transfer", "contains", "Self Transfer", "Self Transfer", None),
    ("axis debit", "contains", "Axis Debit", "Transfer", None),
    ("kotak stp", "contains", "Kotak STP", "Transfer", None),

    # -- Taxes (iter-16 D1 / tracker #153) --
    # "CBDT TIN 2 0" is how the Central Board of Direct Taxes payee renders in the SMS.
    # 
    ("cbdt tin", "contains", "Income Tax (CBDT)", "Taxes", "Central Board of Direct Taxes"),
    ("cbdt", "contains", "Income Tax (CBDT)", "Taxes", "Central Board of Direct Taxes"),
]


_BUSINESS_KEYWORDS = {
    "pvt", "ltd", "limited", "llp", "inc", "corp", "enterprise", "enterprises",
    "private", "association", "foundation", "group", "services", "solutions",
    "technologies", "tech", "digital", "india", "super", "market", "markets",
    "store", "stores", "traders", "trading", "pharma", "hospital", "clinic",
    "restaurant", "cafe", "hotel", "lounge", "salon", "studio", "auto",
    "motors", "tower", "towers", "estate", "estates", "colony", "floor",
    "caterers", "chicken", "fresh", "gourmet", "samosa", "grand",
    "insurance", "finance", "finserv", "mutual", "fund",
    "amortization", "principal", "interest", "debit", "adjustment",
    "commerce", "retail", "wholesale",
    # Parser placeholders ("HSBC credit", "Unknown credit") are not people. They were hidden as
    # CRED/Finance by the "cred"-in-"credit" accident; without that they read as names (iter-18 M14).
    "credit", "unknown",
}


def _word(word, plural=False):
    """Whole word: no letter may touch it on either side (digits and punctuation may)."""
    return re.compile(r"(?<![a-z])" + re.escape(word) + (r"s?" if plural else "") + r"(?![a-z])")


def _word_start(word):
    """Start of a word: card descriptors run a brand into the next word (UBERTRIP, OYOROOMS)."""
    return re.compile(r"(?<![a-z])" + re.escape(word))


# iter-18 M14 — patterns that are only a signal as a word, never as a fragment of another word.
# Plain substring matching filed TRENT (Westside/Zudio's parent) under Rent & Housing, SRI SAI
# CHEMISTS under EMI, TOYOTA KIRLOSKAR under Travel (OYO), every "HSBC credit" / "CREDIT CARD
# PAYMENT" / "CASHBACK CREDIT" under CRED/Finance, and SBI e-mandate debits ("...registered by you
# at merchant", squeezed to "youatmerchant" by the no-space pass) under ATM Withdrawal. Applies to
# alias patterns and fallback keywords alike; a guarded pattern never takes part in the no-space
# pass. Each choice below was checked against every raw_description in the live DB (2026-10-20):
# the left column lists what must not match, the right what still does.
_GUARDED = {
    "rent": _word("rent"),                 # TRENT, CURRENT, PARENT, RENTOMOJO   | "HOUSE RENT"
    "emi": _word("emi", plural=True),      # CHEMISTS, PREMIUM, ACADEMIC         | "EMI MOKOBARA", "OFFUS EMI,PRIN", "(Pay in EMIs)"
    "cred": _word("cred"),                 # CREDIT (69 live rows)               | "CRED", "CRED Club", "CRED.CLUB"
    "atm": _word("atm"),                   # "at merchant" (6 live e-mandates)   | "HDFC ATM", "NWD-ATM"
    "sip": _word("sip"),                   # GOSSIP, SIPCOT                      | "SIP 123"
    "lab": _word("lab", plural=True),      # COLLAB, SLAB, LABEL                 | "XYZ LABS"
    "bus": _word("bus"),                   # BUSINESS, OMNIBUS                   | "APSRTC BUS" (REDBUS has an alias)
    "cab": _word("cab", plural=True),      # CABLE (its own Bills keyword)       | "CITY CABS"
    "mess": _word("mess"),                 # MESSAGE, MESSRS                     | "BOYS MESS"
    "flat": _word("flat"),                 # FLATMATE                            | "FLAT 402"
    "shell": _word("shell"),               # SHELLY, SEASHELL                    | "SHELL INDIA"
    "acko": _word("acko"),                 # JACKO, "back office" squeezed       | "ACKO GENERAL"
    "mall": _word("mall"),                 # names that start with it (2 live person payments) | "VENUS SHOPPING MALL"
    "amul": _word("amul"),                 # a name that ends in it (1 live person payment)     | "AMUL ICE CREAM"
    "devara": _word("devara"),             # DEVARAJ, DEVARAKONDA (names)        | "...TO 55 6 DEVARA"
    "trent": _word("trent"),               # TRENTO                              | "TRENT LIMITED"
    "oyo": _word_start("oyo"),             # TOYOTA                              | "OYO ROOMS", "OYOROOMS"
    "uber": _word_start("uber"),           # KUBERA, TUBER                       | "UBER INDIA", "UBERTRIP"
    "eye": _word_start("eye"),             # KEYES, HEYEY                        | "EYE CARE", "EYEWEAR"
    "game": _word_start("game"),           # (left side only)                    | "GAMES", "GAMEZONE"
    "chemist": _word_start("chemist"),     # ALCHEMIST (a cafe)                  | "SRI SAI CHEMISTS"
    "upi ": _word("upi"),                  # a surname containing it (live, squeezed pass), GROUPINDIA | "UPI-POMPEII PIZZA"
    "canva": re.compile(r"(?<![a-z])canva(?!s)"),   # CANVAS                     | "CANVA*", "CANVAI6000000000009"
    "hospital": re.compile(r"hospital(?!ity)"),     # GMR HOSPITALITY (live, filed Health) | "KimsHospital"
    "mart": re.compile(r"mart(?![a-z])"),          # surnames that contain it (a Rs 14,000 person payment) | "DMART", "RSP*INSTAMART"
}
# Not guarded, on evidence: "fee" and "tax" only occur inside multi-word patterns ("late fee",
# "annual fee"); "shop", "store", "food", "retail" match mostly inside legitimate run-together
# descriptors (Mithaishop, SwiggyFoodBangalore) and no live row was mis-filed by them.


def _contains(pattern, text):
    rx = _GUARDED.get(pattern)
    if rx is not None:
        return rx.search(text) is not None
    return pattern in text


# iter-203 E3: everyday-spending categories the owner files payments to people into by hand (the
# maid, auto drivers, a friend who paid for dinner). None is an outflow group, so they count as
# Spending (routes.analytics.category_group).
OWNER_CATEGORIES = ("Household help", "Local transport", "Outings & misc")


def known_categories(conn=None):
    """Every category a transaction can be filed under: the owner's, the alias table's, the keyword
    fallbacks' and any already in use."""
    conn = conn or get_db()
    names = set(OWNER_CATEGORIES) | set(_KEYWORDS) | {c for (_p, _m, _n, c, _x) in SEED_ALIASES}
    names |= {"Uncategorized", "Transfer", "Family Remittance", "Self Transfer", "Finance",
              "Investment", "Home Construction", "Loan Repayment", "EMI"}
    for r in conn.execute("SELECT DISTINCT category FROM merchant_aliases").fetchall():
        names.add(r["category"])
    for r in conn.execute("SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL").fetchall():
        names.add(r["category"])
    return sorted(n for n in names if n and n.strip())


_KEYWORDS = {
    # "catering" / "juice" / "tea point" / "chat bhandar": canteens and stalls paid by UPI from a bank
    # account, which the person-name rule used to file as transfers (iter-203 E4)
    "Food & Dining": ["restaurant", "cafe", "pizza", "burger", "food", "bakery", "biryani",
                      "kitchen", "dhaba", "eatery", "canteen", "mess", "tiffin", "catering", "juice",
                      "tea point", "chat bhandar"],
    "Groceries": ["grocery", "supermarket", "kirana", "provision", "vegetables", "fruits"],
    # "fabric", "silks", "handloom": the Vijayawada clothing shops on the 0026 card (RAMA FABRICS,
    # KAMADHENU SILKS, K S R HANDLOOMS) — once card spends stopped becoming Transfer they would
    # otherwise be Uncategorized (iter-18 M14).
    "Shopping": ["store", "shop", "mart", "retail", "mall", "fashion", "apparel", "clothing",
                 "garment", "textile", "jewel", "electronics", "fabric", "silks", "handloom"],
    "Transport": ["metro", "bus", "parking", "toll", "taxi", "cab", "auto stand"],
    "Travel": ["travel", "flight", "airline", "railway", "booking", "hotel", "resort"],
    "Fuel": ["fuel", "diesel", "petrol", "petroleum", "filling station"],
    "Health": ["pharmacy", "medical", "hospital", "doctor", "clinic", "diagnostic",
               "pathlab", "lab", "dental", "eye", "optician", "chemist", "health care", "healthcare"],
    # There was no Services fallback at all, so every new salon/barber landed in
    # Uncategorized until someone hand-wrote a SEED_ALIASES entry. "spa" and "beauty"
    # are deliberately excluded — they collide with spares/space/beauty-products.
    "Services": ["salon", "saloon", "barber", "parlour", "parlor", "grooming"],
    "Education": ["school", "college", "tuition", "education", "course", "coaching",
                  "academy", "institute", "university"],
    "Entertainment": ["cinema", "movie", "game", "theatre", "theater", "amusement"],
    "Bills & Utilities": ["electricity", "water", "broadband", "internet", "recharge",
                          "postpaid", "prepaid", "dth", "cable"],
    "ATM Withdrawal": ["atm", "cash withdrawal", "withdrawn"],
    "Bank Charges": ["annual fee", "late fee", "finance charge"],
    "Transfer": ["neft", "imps", "rtgs"],
    # "insuranc": SBI's account alert cuts the payee to 20 characters ("SBI GENERAL INSURANC")
    "Insurance": ["insuranc", "premium"],
    "Investment": ["mutual fund", "demat", "sip"],
    "Rent & Housing": ["rent", "housing", "maintenance", "society", "apartment",
                       "flat", "pg ", "hostel"],
}


def _is_person_name(desc):
    if not desc:
        return False
    cleaned = " ".join(desc.split())
    words = cleaned.split()
    if len(words) < 1 or len(words) > 6:
        return False
    if "@" in cleaned:
        return False
    if any(c.isdigit() for c in cleaned):
        return False
    lower_words = {w.lower().rstrip(".") for w in words}
    if lower_words & _BUSINESS_KEYWORDS:
        return False
    for w in words:
        stripped = w.replace(".", "")
        if not stripped.isalpha():
            return False
    return True


# --- iter-203 E4: who is a business ---------------------------------------------------------------
# A bank-account UPI payment names only the payee, and the person-name rule used to file every
# two-word business as a payment to a person ("SVC CATERINGS", "M S ... HEALTH CARE"). A payee is a
# business when its name carries one of these words, when its UPI ID has a merchant shape, or when the
# bank says the debit ran on a UPI AutoPay mandate (context 'upi_mandate'; only merchants hold one).
_MERCHANT_WORDS = {
    "pvt", "ltd", "limited", "llp", "inc", "corp", "corporation", "private", "enterprise", "enterprises",
    "services", "service", "solutions", "technologies", "technology", "store", "stores", "traders",
    "trading", "mart", "foods", "caterings", "catering", "caterers", "restaurant", "cafe", "hotel", "hotels",
    "bakery", "bakers", "sweets", "juice", "center", "centre", "point", "bhandar", "health", "healthcare",
    "hospital", "clinic", "pharmacy", "pharma", "medicals", "medical", "chemists", "agencies", "agency",
    "associates", "industries", "constructions", "builders", "developers", "motors", "electricals",
    "electronics", "textiles", "garments", "fashions", "salon", "academy", "travels", "tours", "logistics",
    "couriers", "systems", "ventures", "exports", "distributors", "suppliers", "wholesale", "retail",
    "kirana", "provisions", "insurance", "insuranc", "supermarket", "courtyard",
}
# Merchant UPI IDs: Paytm / PhonePe / BharatPe QR handles, Vyapar, payment-gateway collect IDs and
# Google Pay for Business. A person's ID is a name or a phone number on a bank's handle.
_MERCHANT_VPA_HANDLE = re.compile(
    r"^(?:paytmqr|paytm[-.]|bharatpe|q\d{6,}$|mab\.?\d|gpay-\d|vyapar\.|upiswiggy)"
    r"|\.rzp$|razorpay|\.payu$|cashfree|pinelabs|mswipe|ezetap", re.I)
_MERCHANT_VPA_PSP = re.compile(r"^(?:okbiz\w*|fbpe|ptys|pty|pta|yespay|ikwik)$", re.I)
_VPA = re.compile(r"\b([a-z0-9][a-z0-9._-]{1,63})@([a-z]{2,20})\b", re.I)


def _merchant_vpa(desc):
    m = _VPA.search(desc or "")
    return bool(m) and bool(_MERCHANT_VPA_HANDLE.search(m.group(1)) or _MERCHANT_VPA_PSP.match(m.group(2)))


def _is_business_name(desc):
    words = {w for w in re.findall(r"[a-z]+", (desc or "").lower())}
    return bool(words & _MERCHANT_WORDS)


# --- iter-203 E10: one payee, every spelling ----------------------------------------------------
# One payee can reach the DB as "SHANTI TRADERS" (UPI), "Shanthi traders" (bank email) and
# "Shanth" (YONO cuts names to 6 characters). Indian names transliterate loosely: th/t, dh/d, bh/b,
# sh/s, doubled vowels, w/v. A multi-word alias matches any description that is the same words under
# that folding; single words never take part (too many people share a first name).
_VARIANT_MIN_LETTERS = 8
# YONO: "Your Ac x1234 debited Rs.5,50,000.00 for transfer to <name, max 6 chars> Ac x9999 dt ..."
_YONO_TRANSFER = re.compile(r"debited\s+(?:INR|Rs\.?)\s*[\d,]+(?:\.\d+)?\s+for\s+transfer\s+to\s", re.I)
YONO_NAME_WIDTH = 6


@lru_cache(maxsize=8192)
def _variant(text):
    t = re.sub(r"[^a-z ]+", " ", (text or "").lower())
    t = re.sub(r"([bcdgjkpst])h", r"\1", t)
    t = re.sub(r"([a-z])\1+", r"\1", t)
    return " ".join(t.replace("w", "v").split())


@lru_cache(maxsize=4096)
def _variant_alias_ok(pattern):
    letters = re.sub(r"[^a-z]", "", pattern.lower())
    return len(pattern.split()) >= 2 and len(letters) >= _VARIANT_MIN_LETTERS and pattern.lower() not in _GUARDED


def _yono_truncated(description, raw_text):
    """True when the description is a YONO payee name the bank may have cut at its width."""
    name = (description or "").strip()
    return bool(raw_text and _YONO_TRANSFER.search(raw_text) and len(name) == YONO_NAME_WIDTH
                and name.isalpha())


def is_merchant_payee(description, context=None):
    """True when an account payment's payee is a business, not a person (iter-203 E4)."""
    return context == "upi_mandate" or _merchant_vpa(description) or _is_business_name(description)


def seed_aliases():
    """Insert the seed aliases, and bring existing seed rows in line with SEED_ALIASES.

    Seed rows used to be INSERT OR IGNORE, so a corrected seed entry (iter-18 M14: the Swiggy
    address patterns, "direct debit payment") never reached a database that already had the old
    one. Only rows still marked source='seed' are updated: anything the owner added or changed
    (POST /aliases -> 'manual', category overrides -> 'user_override', 'seed_v2') is left alone."""
    conn = get_db()
    inserted = 0
    for pattern, match_type, merchant, category, notes in SEED_ALIASES:
        try:
            conn.execute(
                "INSERT INTO merchant_aliases (pattern, match_type, merchant_name, category, source, notes) "
                "VALUES (?, ?, ?, ?, 'seed', ?) "
                "ON CONFLICT(pattern) DO UPDATE SET match_type = excluded.match_type, "
                "merchant_name = excluded.merchant_name, category = excluded.category, notes = excluded.notes "
                "WHERE merchant_aliases.source = 'seed'",
                (pattern, match_type, merchant, category, notes)
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


_CARD_CONTEXTS = {"card", "cc", "meal_card", "email-card"}
_GATEWAY_CATEGORY = "Pass-Through"


def _alias_matches(pattern, match_type, text):
    if match_type == "contains":
        return _contains(pattern, text)
    if match_type == "startswith":
        return text.startswith(pattern)
    if match_type == "regex":
        return bool(re.search(pattern, text))
    return False


_LOAN_RE = re.compile(r"\bloan\s*(?:a/c|ac\b|acct|account|repayment)", re.I)
# The counterparty account an account-to-account transfer names: "IMPS to a/c XXXXXXX706",
# "NEFT via Online to SBOI A/c ***4321 Owner SBI", "towards account 111-222***-706".
_COUNTERPARTY_ACCT_RE = re.compile(r"(?:a/c|\bac\b|acct|account)(?:\s*no\.?)?\s*([Xx\*\d][Xx\*\d-]*)", re.I)


# Account movements that name no merchant (iter-18 verifier, 1442): HSBC's standing debit prints
# only "as DEBIT" / "as per your request", SBI's internet-banking alert only "INB txn" (filed as
# Bills & Utilities by the "internet" keyword), HDFC's "to a/c **9999" only the payee's account.
_BARE_ACCOUNT_DEBIT = re.compile(r"^\s*(?:as\s+)?(?:debit|(?:as\s+)?per\s+your\s+request)\s*\.?\s*$", re.I)
_INTERNET_BANKING = re.compile(r"\binternet\s+banking\b|\bINB\b", re.I)


def account_movement(description):
    """The merchant label for an account-to-account movement that names no merchant, or None."""
    d = description or ""
    if _BARE_ACCOUNT_DEBIT.search(d):
        return "Account debit"
    if _INTERNET_BANKING.search(d):
        return "Internet banking transfer"
    for m in _COUNTERPARTY_ACCT_RE.finditer(d):
        if re.search(r"\d{3}", m.group(1)):
            return "Bank Transfer"
    return None


def own_account_transfer(conn, description):
    """'Self Transfer' / 'Finance' when the counterparty account in an account-to-account
    transfer is one of the owner's own (iter-18, staging finding: Rs 4.5L moved HSBC -> own SBI ->
    own HSBC in August surfaced as Rs 17L "sent to people"). Own = every account and card in the
    cards table (active or not). The parsers put the COUNTERPARTY in raw_description, never the
    source account, so a match here means money stayed with the owner. Banks mask to 3 or 4
    trailing digits, so 3-digit tails are compared on 3 digits and longer tails on 4."""
    rows = conn.execute("SELECT last4, active FROM cards").fetchall()
    own4 = {r["last4"][-4:]: r["active"] for r in rows if r["last4"] and len(r["last4"]) >= 4}
    own3 = {r["last4"][-3:]: r["active"] for r in rows if r["last4"] and len(r["last4"]) >= 3}
    for m in _COUNTERPARTY_ACCT_RE.finditer(description or ""):
        tail = re.search(r"(\d+)$", m.group(1))
        if not tail:
            continue
        digits = tail.group(1)
        active = own4.get(digits[-4:]) if len(digits) >= 4 else own3.get(digits[-3:])
        if active is None:
            continue
        # paying one of the owner's own active credit cards from an account is a card bill payment
        return ("Card bill payment", "Finance") if active else ("Self Transfer", "Self Transfer")
    return None


def categorize(description, context=None, raw_text=None):
    """Returns: (merchant_name, category, confidence)

    `context` is where the text came from ('cc'/'card'/'email-card' for credit-card spends,
    'upi'/'bank' for account movements, None when unknown). It only gates rules that are wrong
    for card spends: a card is swiped at a shop, so a merchant that merely *looks* like a person's
    name ("ZUDIO", "RAMA FABRICS") must not become a Transfer, and neither may a card payment to a
    merchant UPI QR (RuPay credit cards on UPI are merchant-only: "paytmqr...@ptys",
    "q600000034@ybl" on a RuPay card), which the VPA aliases file as Transfer (iter-18 M14).

    `raw_text` (the full message, when the caller has it) is only read to recognise the payee: a
    payee rule keyed on an account the description does not print (iter-203 E3/E10)."""
    if not description:
        return ("Unknown", "Uncategorized", "low")

    desc_lower = description.lower().strip()
    on_card = context in _CARD_CONTEXTS
    if context == "atm":
        # an ATM alert's description is the machine's location ("+MAIN ROAD COLONY"), which the keyword
        # rules read as a merchant (Rent & Housing); the parser already knows it is cash
        return (description, "ATM Withdrawal", "high")
    conn = get_db()

    # iter-203 E3: the owner's own payee rules come before any guess (modules.payee_rules)
    from modules import payee_rules
    rule = payee_rules.match(conn, description, raw_text)
    if rule is not None:
        return (rule["merchant_name"] or description, rule["category"], payee_rules.RULE_CONFIDENCE)

    if not on_card:
        # Debt service and money that never left the owner come before any merchant guess.
        if _LOAN_RE.search(description):
            return ("Loan Repayment", "Loan Repayment", "high")
        own = own_account_transfer(conn, description)
        if own:
            return (own[0], own[1], "high")

    aliases = conn.execute(
        "SELECT pattern, match_type, merchant_name, category FROM merchant_aliases ORDER BY length(pattern) DESC"
    ).fetchall()

    # A payment gateway ("Razorpay*Swiggy LimiteBengaluru") is only the pipe. Its alias is longer
    # than the merchant's, so it used to win; when the merchant behind it is named too, the
    # merchant wins and the gateway is the fallback (iter-18 M14: 19 live Swiggy orders would
    # otherwise have been re-filed as Razorpay / Pass-Through). Behind a gateway the payee is a
    # business, so a Transfer alias (a VPA suffix) does not override it either.
    gateway = None
    # iter-203 E4: an account payment to a business must not become a payment to a person through
    # the generic UPI-ID aliases ("@ybl" -> "UPI Transfer") or the person-name rule below. Aliases that
    # NAME a payee (PhonePe, Paytm wallet loads, a family member) still apply.
    merchant_payee = not on_card and is_merchant_payee(description, context)
    # A merchant UPI ID or an AutoPay mandate is a shop for certain — like a card spend, no Transfer
    # alias applies (the "paytm" wallet alias would otherwise take every "paytmqr...@ptys" QR).
    strict_merchant = not on_card and (context == "upi_mandate" or _merchant_vpa(description))

    def usable(alias):
        if (on_card or strict_merchant) and alias["category"] == "Transfer":
            return False
        if merchant_payee and alias["merchant_name"] == "UPI Transfer":
            return False
        if gateway is not None and alias["category"] in (_GATEWAY_CATEGORY, "Transfer"):
            return False
        return True

    for alias in aliases:
        if not usable(alias):
            continue
        if _alias_matches(alias["pattern"].lower(), alias["match_type"], desc_lower):
            if alias["category"] == _GATEWAY_CATEGORY:
                gateway = alias
                continue
            return (alias["merchant_name"], alias["category"], "high")

    # Second pass with the spaces squeezed out, for descriptors that run words together
    # ("WWWBIGBASKETCOM", "MAKE MY TRIP" vs "makemytrip"). Guarded words are skipped: squeezing
    # is exactly what turned "at merchant" into "atm".
    desc_nospace = desc_lower.replace(" ", "")
    for alias in aliases:
        pattern = alias["pattern"].lower()
        if pattern in _GUARDED or not usable(alias):
            continue
        pattern_nospace = pattern.replace(" ", "")
        match_type = alias["match_type"]
        matched = False
        if match_type == "contains":
            matched = pattern_nospace in desc_nospace
        elif match_type == "startswith":
            matched = desc_nospace.startswith(pattern_nospace)
        if matched:
            if alias["category"] == _GATEWAY_CATEGORY:
                gateway = gateway or alias
                continue
            return (alias["merchant_name"], alias["category"], "high")

    # iter-203 E10, third pass: a spelling variant of a multi-word payee alias ("Shanthi traders"
    # for "shanti traders").
    desc_variant = f" {_variant(description)} "
    for alias in aliases:
        if alias["match_type"] != "contains" or not usable(alias) or not _variant_alias_ok(alias["pattern"]):
            continue
        if alias["category"] == _GATEWAY_CATEGORY:
            continue
        if f" {_variant(alias['pattern'])} " in desc_variant:
            return (alias["merchant_name"], alias["category"], "high")

    # ... and a YONO name cut at 6 characters ("Shanth") that starts exactly one payee alias. Only
    # where the bank's wording truncates (a full name elsewhere is a different person), only at the
    # cut width (a shorter name is complete), only when the prefix is unambiguous.
    if not on_card and _yono_truncated(description, raw_text):
        prefix = _variant(description)
        hits = {}
        for alias in aliases:
            if alias["match_type"] != "contains" or not usable(alias) or alias["category"] == _GATEWAY_CATEGORY:
                continue
            if len(alias["pattern"].split()) >= 2 and _variant(alias["pattern"]).startswith(prefix):
                hits[(alias["merchant_name"], alias["category"])] = alias
        if len(hits) == 1:
            merchant, category = next(iter(hits))
            return (merchant, category, "medium")

    if gateway is not None:
        return (gateway["merchant_name"], gateway["category"], "high")

    if not on_card:
        movement = account_movement(description)
        if movement:
            return (movement, "Transfer", "medium")

    for category, keywords in _KEYWORDS.items():
        for kw in keywords:
            if _contains(kw, desc_lower):
                return (description, category, "medium")

    if _contains("emi", desc_lower) or "autopay" in desc_lower:
        return (description, "EMI", "medium")

    if not on_card and not merchant_payee and _is_person_name(description):
        return (description, "Transfer", "medium")

    return (description, "Uncategorized", "low")


# iter-203 E9: learn_from_override() used to run on every manual correction and store the first 40
# characters of the description as a 'contains' alias (INSERT OR REPLACE, so it could overwrite a
# seed alias). One re-filed "DEBIT" row would have relabelled every future row containing "debit";
# one re-filed Swiggy order moved every later Swiggy order. It is gone: a correction changes its own
# row only, and a rule exists only when the owner asks for one (modules.payee_rules, E3).


# --- Context of a stored row (iter-18 M14 / HP4) ---------------------------------------------

def card_numbers(conn):
    """Last-4s that identify one of the owner's ACTIVE cards: the SMS number and, where it
    differs, the number printed on the statement PDF. Bank accounts and debit cards live in the
    same table with active=0, so "is this last4 an active card" is what separates a card spend
    from an account movement."""
    out = set()
    for r in conn.execute("SELECT last4, pdf_card_last4 FROM cards WHERE active = 1").fetchall():
        for value in (r["last4"], r["pdf_card_last4"]):
            if value:
                out.add(str(value))
    return out


def context_for_row(source, card_last4, text, cards):
    """The categorisation context a stored row would have been given by insert_transaction().

    source 'card' (statement PDF line) -> 'card'; an SMS / email naming an active card ->
    'cc' / 'email-card'; anything else is an account movement -> 'upi' when the text shows a UPI
    payment, else 'bank'."""
    if source == "card":
        return "card"
    if source == "sms" and text:
        # the context the SMS parser itself gives this message, exactly as insert time did — a
        # Pluxee meal-card spend is 'meal_card' (not an account 'bank' movement whose person-name
        # rule re-filed COMPASS / VIJETHA SUP as Transfer on every recategorise, iter-18 verifier)
        try:
            from modules.sms.parser import parse_single_sms
            parsed = parse_single_sms(text)
        except Exception:
            parsed = None
        if parsed and parsed.get("source"):
            return parsed["source"]
    if card_last4 and str(card_last4) in cards:
        return "email-card" if source == "email" else "cc"
    t = (text or "").lower()
    return "upi" if ("upi" in t or "@" in t) else "bank"


def _upi_mapped(row, mappings):
    """True when the row's merchant + category are exactly what a upi_mappings row assigns to it.

    modules.upi.apply_mappings writes brand/category with confidence 'high' and leaves no other
    mark, so the mapping itself is the evidence: the row's description contains the mapped VPA
    (or name pattern, for mappings made through /upi/classify) and it carries that mapping's
    labels. Matching is case-insensitive like the LIKE apply_mappings uses."""
    desc = (row["raw_description"] or "").lower()
    for m in mappings:
        keys = [k.lower() for k in (m["upi_id"], m["name_pattern"]) if k]
        if not any(k in desc for k in keys):
            continue
        if row["category"] == m["category"] and (not m["brand"] or row["merchant"] == m["brand"]):
            return True
    return False


def recategorize_with_context(conn, dry_run=True, samples_per_transition=3, row_filter=None):
    """Re-run categorize(raw_description, context) over every stored row (iter-18 M14).

    Rows are never touched when a person or a deliberate pass decided their label:
      - a manual override (a category_overrides row, or confidence 'manual');
      - a UPI-mapping label (see _upi_mapped);
      - an email-receipt label (confidence 'enriched': the Swiggy/Amazon/Uber order email named
        the merchant, which is stronger evidence than the description);
      - a card bill payment (kind 'bill_payment');
      - a label a statement line inherited from its linked SMS/email copy at merge time.
    Returns {changed, by_transition: {"old→new": n}, samples, merchant_renamed, scanned,
    skipped: {...}, updated, dry_run}. `changed` counts category changes; a row whose merchant
    name alone changes (e.g. "Rent/Housing" -> "Swiggy" with the same category) is counted in
    merchant_renamed. With dry_run=False both are written.

    `row_filter(row, new_merchant, new_category) -> bool` (iter-203) limits the pass to the changes a
    caller is responsible for (e.g. only rows leaving "Transfer"); rows it rejects are counted in
    skipped['filtered'] and left as they are."""
    cards = card_numbers(conn)
    overridden = {r["transaction_id"] for r in conn.execute(
        "SELECT DISTINCT transaction_id FROM category_overrides WHERE transaction_id IS NOT NULL").fetchall()}
    mappings = conn.execute("SELECT upi_id, name_pattern, brand, category FROM upi_mappings").fetchall()
    rows = conn.execute(
        "SELECT id, date, source, card_last4, raw_description, raw_text, merchant, category, confidence, kind, "
        "is_duplicate, matched_txn_id FROM transactions ORDER BY id"
    ).fetchall()
    # A label a statement line took from its better-described copy when the merge linked them
    # (modules.merge.apply_link): the copy said "Self Transfer"/"Family Remittance", the line only
    # carries a bank code. Re-deriving from the line's own text would undo that (iter-18 verifier:
    # Rs 90,000 of Self Transfer would have turned into spend).
    # iter-203: a row is only held back while a copy still vouches for its label. When the copy is
    # re-derived to the same new category as the row itself (both simply read the same payee, e.g. an
    # SMS and its e-mail twin both filed "SVC CATERINGS" as a transfer), the row moves with it in the
    # same pass instead of needing a second run.
    inherited_copies = defaultdict(list)
    for r in conn.execute(
        "SELECT w.id AS w, l.id AS l FROM transactions w JOIN transactions l ON l.matched_txn_id = w.id "
        "AND l.is_duplicate = 1 WHERE l.category = w.category AND COALESCE(l.merchant, '') = COALESCE(w.merchant, '') "
        "AND l.source <> w.source").fetchall():
        inherited_copies[r["w"]].append(r["l"])

    skipped = Counter()
    transitions = Counter()
    samples = defaultdict(list)
    updates = []
    renamed = 0
    planned = []
    for r in rows:
        if not r["raw_description"]:
            skipped["no_description"] += 1
            continue
        if r["confidence"] == "manual" or r["id"] in overridden:
            skipped["manual"] += 1
            continue
        if r["confidence"] == "enriched":
            skipped["enriched"] += 1
            continue
        if r["confidence"] == "paired":
            # an own-account transfer / card autopay / reversal labelled from its partner row
            # (modules.pairing) — a description guess must not undo it
            skipped["paired"] += 1
            continue
        if mappings and _upi_mapped(r, mappings):
            skipped["upi_mapping"] += 1
            continue
        if r["kind"] == "bill_payment":
            # a card bill payment (incl. statement payment lines the iter-18 PDF fixes relabelled
            # Finance) is never re-filed as spend by a description guess
            skipped["bill_payment"] += 1
            continue
        ctx = context_for_row(r["source"], r["card_last4"], r["raw_text"] or r["raw_description"], cards)
        merchant, category, confidence = categorize(r["raw_description"], context=ctx, raw_text=r["raw_text"])
        planned.append((r, ctx, merchant, category, confidence))

    # iter-203 E4 (verifier round 2): a copy of a payment the bank itself marks as a merchant's (a UPI
    # AutoPay mandate, a merchant UPI ID) takes that row's label. Its e-mail twin ("debited ... to VPA
    # cloudbill@axisbank Acmecloud") reads like a payment to a person and kept vouching for Transfer.
    strict = {r["id"]: (merchant, category, confidence) for r, ctx, merchant, category, confidence in planned
              if not r["is_duplicate"] and ctx not in _CARD_CONTEXTS
              and (ctx == "upi_mandate" or _merchant_vpa(r["raw_description"]))}
    if strict:
        planned = [(r, ctx, *strict[r["matched_txn_id"]])
                   if r["is_duplicate"] and r["matched_txn_id"] in strict else (r, ctx, m, c, conf)
                   for r, ctx, m, c, conf in planned]

    new_category = {r["id"]: category for r, _ctx, _m, category, _c in planned}
    for r, ctx, merchant, category, confidence in planned:
        copies = inherited_copies.get(r["id"])
        if copies and not all(new_category.get(cid) == category for cid in copies):
            skipped["inherited_from_copy"] += 1
            continue
        old_category = r["category"] or "Uncategorized"
        if category == old_category and merchant == r["merchant"]:
            continue
        if row_filter is not None and not row_filter(r, merchant, category):
            skipped["filtered"] += 1
            continue
        updates.append((merchant, category, confidence, r["id"]))
        if category == old_category:
            renamed += 1
            continue
        key = f"{old_category}→{category}"
        transitions[key] += 1
        if len(samples[key]) < samples_per_transition:
            samples[key].append({"id": r["id"], "date": r["date"], "context": ctx,
                                 "raw_description": r["raw_description"][:60], "merchant": merchant})

    if not dry_run and updates:
        # Autocommit, one statement per row: a re-run converges, so an interrupted pass is safe.
        conn.executemany("UPDATE transactions SET merchant = ?, category = ?, confidence = ? WHERE id = ?",
                         updates)
        conn.commit()

    return {
        "dry_run": dry_run,
        "scanned": len(rows),
        "changed": sum(transitions.values()),
        "merchant_renamed": renamed,
        "updated": 0 if dry_run else len(updates),
        "by_transition": dict(transitions.most_common()),
        "samples": dict(samples),
        "skipped": dict(skipped),
    }
