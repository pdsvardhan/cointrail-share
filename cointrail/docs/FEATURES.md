# Cointrail — Complete Features Guide

_Last updated: March 2026_

This document explains everything that Cointrail does, how it works, and why it works the way it does. It is written for humans — not developers. If you are a friend, a future collaborator, or just someone curious about how one person tracks every rupee flowing through 10+ credit cards and a dozen bank accounts without manually entering a single transaction, this is for you.

---

## Table of Contents

1. [What is Cointrail?](#1-what-is-cointrail)
2. [How Your Data Gets In (The Ingest Pipeline)](#2-how-your-data-gets-in-the-ingest-pipeline)
3. [How Data Gets Processed](#3-how-data-gets-processed)
4. [The Dashboard](#4-the-dashboard)
5. [The FIRE Calculators](#5-the-fire-calculators)
6. [Credit Card Management](#6-credit-card-management)
7. [Architecture (How It All Fits Together)](#7-architecture-how-it-all-fits-together)
8. [Data Security and Privacy](#8-data-security-and-privacy)
9. [How to Add a New Credit Card](#9-how-to-add-a-new-credit-card)
10. [How to Fix Miscategorized Transactions](#10-how-to-fix-miscategorized-transactions)
11. [What is Automated vs What Needs Manual Work](#11-what-is-automated-vs-what-needs-manual-work)

---

## 1. What is Cointrail?

Cointrail is a personal finance dashboard built specifically for the messy, complicated reality of Indian banking. If you have ever tried to answer a simple question like "how much did I spend on food last month?" and realized you would need to log into four different bank apps, download six PDF statements, and cross-reference a bunch of SMS messages — Cointrail solves that problem.

### What It Actually Does

Cointrail tracks **all** credit card spending, UPI transactions, and bank debits/credits across **11 credit cards from 5 banks** (HDFC, HSBC, ICICI, SBI, and Axis). The entire thing is automated. Data flows in from three sources — your phone's SMS messages, your Gmail inbox, and credit card statement PDFs — without you ever needing to manually type in a transaction.

As of March 2026, the system has ingested over **1,132 transactions** from real data — 949 from SMS messages and 183 from credit card statement PDFs. It covers transaction history going back to December 2025.

### Why It Exists

Most personal finance apps in India either:

- Require you to manually enter every expense (nobody keeps that up)
- Need you to share your bank login credentials with a third-party company (risky)
- Only work with one or two banks
- Cannot read credit card statement PDFs
- Do not understand UPI at all

Cointrail takes a different approach: it reads the data that banks already send you (SMS alerts, email statements) and processes it automatically on your own private server. No bank credentials are shared with anyone. No data leaves your home network.

### Who It Is Built For

Right now, Cointrail is a single-user system built for one person. But the architecture is designed so that it can eventually support multiple users, family dashboards, and potentially a public release.

---

## 2. How Your Data Gets In (The Ingest Pipeline)

Every transaction that shows up on your Cointrail dashboard got there through one of three roads. Think of these as three separate conveyor belts, each carrying financial data from a different source, all feeding into the same central warehouse.

### Source 1: SMS from Your Phone (Real-Time)

Every time you swipe a credit card at a shop, make a UPI payment through Google Pay or PhonePe, or receive money in your bank account, your bank sends you an SMS. In India, these transaction SMS messages are incredibly reliable — banks are required by the RBI to send them. They look something like this:

**Example HDFC Credit Card SMS:**

> Rs.1,234.50 spent on HDFC Bank Card x9434 at SWIGGY on 2026-03-15

**Example SBI Card SMS:**

> Transaction of Rs.2,500.00 at AMAZON has been debited from your Card ending 6792 on 15/03/26

**Example HDFC UPI SMS:**

> Sent Rs.500.00 From HDFC Bank A/C x8154 To swiggy@ybl On 2026-03-15

These SMS messages arrive on the phone within seconds of the transaction. The question is: how do you get them from your phone to your server?

**How Tasker Works (The Phone Side)**

Tasker is an Android automation app. Think of it as an "If This Then That" for your phone, but much more powerful. Here is what our Tasker setup does:

1. Tasker watches for every incoming SMS
2. When an SMS arrives, Tasker immediately sends it to the ingest service via an HTTP POST request to `ingest.example.com/sms`
3. The request contains the SMS body text, the sender's phone number, and the timestamp
4. This happens in the background — you never see it, it never interrupts you, and it takes less than a second

So literally within 2 seconds of swiping your credit card at a restaurant, the transaction data is already sitting on your home server, waiting to be parsed.

**What Gets Sent:** The raw SMS text, the sender number (like "HDFCBK" or "SBIINB"), and the exact timestamp.

**What Does NOT Get Sent:** Your phone does not send personal messages, WhatsApp messages, or anything else. Only SMS from bank senders.

### Source 2: Email from Gmail (Hourly Sync)

Every month, each of your banks emails you a credit card statement. This is a PDF file attached to an email. The email usually comes from addresses like `creditcardstatement@mail.hsbc.co.in` or `statements@sbicard.com`.

The system syncs with Gmail every hour (via the Gmail API with OAuth2 authentication — meaning you authorized it once, and it can now read your emails without knowing your password). Here is what the sync process does:

1. **Fetch new emails:** It asks Gmail "give me all emails since the last time I checked." On the first run, it fetched all 762 emails in the inbox. After that, it only grabs new ones.

2. **Classify each email:** Not every email is financial. The system looks at the sender address and subject line to determine if this is a bank email, a credit card statement, a transaction alert, or just regular mail. Out of 762 emails synced, 308 were classified as financial.

3. **Find CC statement emails:** Emails from known credit card statement senders (like `creditcardstatement@mail.hsbc.co.in`, `credit_cards@icicibank.com`, `statements@sbicard.com`, `cc.statements@axisbank.com`, `alerts@hdfcbank.net`) are flagged for PDF extraction.

4. **Download the PDF attachment:** This is trickier than it sounds because of how email works internally. An email is not a simple text file — it is a nested structure called MIME (Multipurpose Internet Mail Extensions). A single email might have:
   - A plain text part
   - An HTML part (the formatted version you see in Gmail)
   - An attachment part
   - And these can be nested inside each other, sometimes three or four levels deep

   The system has to dig through this MIME tree to find the actual PDF file. Some banks nest their PDFs inside a multipart/mixed container which is inside a multipart/alternative container which is inside the main message. It is like opening a box to find another box to find another box with the PDF inside.

5. **Save the PDF:** Once extracted, the PDF is saved to the server's statements directory, ready for parsing.

### Source 3: Credit Card Statement PDFs (Batch Processing)

Credit card statement PDFs from Indian banks are almost always password-protected. Each bank has its own password scheme:

- **HDFC:** Usually your date of birth in a specific format
- **ICICI:** Usually your PAN number
- **HSBC:** A numeric code (often account-related)
- **SBI:** Varies by card type
- **Axis:** Similar to HDFC

The system stores known passwords in a secure file on the server. When a new PDF lands in the statements directory (either downloaded from email or manually uploaded), the processing goes like this:

1. **Check if encrypted:** Try to open the PDF. If it fails with a password error, it is encrypted.
2. **Try all known passwords:** The system loops through every stored password, trying each one. When one works, it decrypts the PDF using a library called `pikepdf` and saves a decrypted copy.
3. **Detect which bank:** Open the first page of the decrypted PDF and search for bank-identifying text. If it says "HDFC BANK" anywhere on page one, it is HDFC. If it says "HSBC", it is HSBC. The system can auto-detect 16 different Indian banks (HDFC, ICICI, SBI, Axis, HSBC, Kotak, Amex, Standard Chartered, IndusInd, IDFC, RBL, Yes, Bank of Baroda, Federal, AU, and OneCard).
4. **Parse with the right parser:** Each bank formats their PDF statement completely differently (more on this in the next section), so the system routes the PDF to the correct bank-specific parser.

---

## 3. How Data Gets Processed

Raw data is useless. An SMS that says "Rs.1234 spent on HDFC Bank Card x9434 at BUNDL TECHNOLOGIES on 2026-03-15" needs to be broken down into structured pieces: the amount (1,234), the card (HDFC ending in 9434), the merchant (Swiggy, because "BUNDL TECHNOLOGIES" is Swiggy's legal entity name), the date (March 15, 2026), and the category (Food & Dining). Here is how each processing step works.

### SMS Parsing: 36+ Regex Patterns for Indian Bank SMS

Every Indian bank writes their SMS alerts in their own unique format. There is no standard. HDFC says "Rs.X spent on HDFC Bank Card xNNNN at MERCHANT on DATE". SBI says "Transaction of Rs.X at MERCHANT has been debited from your Card ending NNNN on DATE". ICICI says "Acct XXNNNN debited Rs.X on DATE to MERCHANT". UPI payments, bank account debits, credits, refunds — each one has its own phrasing.

The SMS parser has **36+ regex patterns**, each designed to match a specific bank's specific SMS format. A regex (regular expression) is like a template with blanks — it describes the structure of the SMS so the system can pull out the important parts.

**Example 1: HDFC Credit Card Spend**

SMS text: `Rs.1,234.50 spent on HDFC Bank Card x9434 at SWIGGY on 2026-03-15`

The regex pattern for this is designed to find:
`Rs.(amount) spent on HDFC Bank Card x(card) at (merchant) on (date)`

Extracted data:

- Amount: 1,234.50
- Card last 4 digits: 9434
- Merchant: SWIGGY
- Date: 2026-03-15
- Bank: HDFC
- Type: debit (money going out)
- Source: credit card

**Example 2: HDFC UPI Send**

SMS text (sometimes multi-line):

```
Sent Rs.500.00
From HDFC Bank A/C x8154
To swiggy@ybl
On 2026-03-15
```

Extracted data:

- Amount: 500.00
- Account last 4: 8154
- Merchant/Payee: swiggy@ybl
- Date: 2026-03-15
- Bank: HDFC
- Type: debit
- Source: UPI

**Example 3: SBI Card Spend**

SMS text: `Transaction of Rs.2,500.00 at AMAZON has been debited from your Card ending 6792 on 15/03/26`

Extracted data:

- Amount: 2,500.00
- Merchant: AMAZON
- Card last 4: 6792
- Date: 2026-03-15 (normalized from 15/03/26)
- Bank: SBI
- Type: debit
- Source: credit card

The parser handles all of the following banks and transaction types:

- **HDFC:** CC spend, CC UPI, UPI send, UPI receive, credit alerts, CC payments, debit card, NEFT/IMPS
- **HSBC:** UPI, credit, CC spend
- **SBI:** Card spend, card foreign currency, UPI
- **ICICI:** Account debit, CC alerts
- **Axis:** CC spend, CC payments
- **PhonePe / Google Pay:** UPI debits and credits

### PDF Parsing: Bank-Specific Statement Parsers

Each bank formats their credit card statement PDF in a completely unique way. There is no standard layout, no standard date format, no standard way of showing amounts. So the system has a dedicated parser for each bank.

**HDFC Statement Format:**
HDFC uses a pipe-delimited format. Each transaction line looks like:

```
15/03/2026| 14:30  SWIGGY ORDER 123456  C 1,234.50
```

The date and time are separated by a pipe character (`|`). The description follows. A `C` before the amount represents the currency symbol (Rupee). If there is a `+` before the `C`, it is a credit (refund or cashback). The parser uses regex to pull apart each line based on this structure. It also handles an older HDFC format that does not include the time field.

**HSBC Statement Format:**
HSBC uses a DDMON format (day + 3-letter month abbreviation, no year). Each line looks like:

```
07FEB  BBPS PMT BBPSDP016038193256  3,867.00 CR
17FEB  IAP Myntra Designs Pvt Ltd BANGALORE  11,757.00
```

"CR" at the end means credit. No "CR" means debit. The year is not on each transaction line — the parser has to extract the year from a "Statement Period" line that appears elsewhere in the PDF (like "05 FEB 2026 To 04 MAR 2026") and apply that year to all transactions.

**ICICI Statement Format:**
ICICI uses a table-style format with columns:

```
15/03/2026  001  AMAZON SELLER SERVICES  2,500.00
```

Date, serial number, description, and then either a debit or credit amount in separate columns. The parser reads each line, checks which column the amount is in, and determines if it is a debit or credit.

**Axis and SBI** have their own layouts as well, each with their own quirks that the respective parsers handle.

All parsers skip non-transaction lines (headers, footers, summaries, "Total Outstanding Balance" lines, etc.) and filter out CC payment lines (since paying your credit card bill is not an expense — it is just moving money between accounts).

### Categorization: The Two-Pass System

Once a transaction is parsed (whether from SMS or PDF), it needs to be assigned a category — Food & Dining, Shopping, Transport, Travel, etc. The system uses a two-pass categorization approach:

**Pass 1: Merchant Alias Table (110+ Known Merchants)**

Indian businesses often show up on your credit card statement under their legal entity name, not the brand name you know them by. For example:

| What appears on your statement | What it actually is   | Category      |
| ------------------------------ | --------------------- | ------------- |
| BUNDL TECHNOLOGIES             | Swiggy                | Food & Dining |
| BUNDL TECH                     | Swiggy                | Food & Dining |
| ANI TECHNOLOGIES               | Ola (the cab company) | Transport     |
| TATA DIGITAL                   | Tata Neu              | Shopping      |
| INSTAMART SVCS                 | Swiggy Instamart      | Groceries     |
| VIACOM18                       | JioCinema             | Entertainment |

The alias table has **110+ mappings** covering all the popular Indian brands across categories:

- **Food & Dining:** Swiggy, Zomato, Chaayos, Starbucks, Dominos, McDonalds, KFC, Subway, Haldirams, Barbeque Nation
- **Groceries:** Swiggy Instamart, BigBasket, Blinkit, Zepto, Dunzo, JioMart, DMart, Ratnadeep
- **Shopping:** Amazon, Flipkart, Myntra, AJIO, Nykaa, Meesho, Tata Neu, Reliance Retail, Decathlon, Lenskart, Croma, Vijay Sales
- **Transport:** Uber, Ola, Rapido
- **Travel:** IRCTC, MakeMyTrip, Goibibo, Cleartrip, Yatra, RedBus, EaseMyTrip, OYO
- **Fuel:** BPCL, HPCL, Indian Oil, FASTag Toll
- **Entertainment:** Netflix, Hotstar, Spotify, YouTube Premium, Prime Video, JioCinema, ZEE5, SonyLIV
- **Subscriptions:** Apple, Google Play
- **Bills & Utilities:** Airtel, Jio, Vi, BESCOM, TSSPDCL, Tata Power, ACT Fibernet

The alias match is case-insensitive and uses "contains" matching, so "BUNDL TECHNOLOGIES PVT LTD BANGALORE" still matches the "bundl technologies" alias.

There is also a **Pass 1.5** that collapses spaces before matching. So "MAKE MY TRIP" (with spaces) will match the alias "makemytrip" (without spaces), which solves a surprisingly common problem where the same merchant appears with different spacing across banks.

**Pass 2: Keyword Fallback**

If no alias matches, the system falls back to keyword scanning. It looks for common words in the transaction description and assigns a likely category:

- Contains "restaurant", "cafe", "pizza", "food" -> Food & Dining
- Contains "fuel", "petrol", "diesel" -> Fuel
- Contains "hospital", "pharmacy", "clinic" -> Health
- Contains "recharge", "broadband", "electricity" -> Bills & Utilities
- Contains "atm", "cash withdrawal" -> ATM Withdrawal
- Contains "annual fee", "late fee", "gst" -> Bank Charges
- Contains "upi", "neft", "imps" -> Transfer
- Contains "insurance", "premium" -> Insurance
- Contains "mutual fund", "demat" -> Investment
- Contains "rent", "housing", "maintenance" -> Rent & Housing
- Contains "emi", "autopay" -> EMI

Each categorization gets a **confidence score**: "high" if it came from the alias table (we are very sure), "medium" if it came from keyword matching (probably right), and "low" if nothing matched (categorized as "Uncategorized").

**How a Transaction Gets Categorized — Full Example:**

A credit card statement line says: `BUNDL TECHNOLOGIES PVT LTD BANGALORE`

1. Pass 1 checks the alias table. "bundl technologies" is in the list, mapped to Swiggy -> Food & Dining.
2. Result: Merchant = "Swiggy", Category = "Food & Dining", Confidence = "high"
3. Done. No need for Pass 2.

Another example: `DR EXAMPLE CLINIC CITYNAME`

1. Pass 1 checks the alias table. No match for "dr rajesh clinic".
2. Pass 1.5 collapses spaces and tries again. Still no match.
3. Pass 2 keyword scan: "clinic" is in the Health keywords list.
4. Result: Merchant = "DR EXAMPLE CLINIC CITYNAME" (kept as-is), Category = "Health", Confidence = "medium"

### Cross-Source Matching: Deduplication and Enrichment

Here is a problem: when you swipe your HDFC credit card at Swiggy, you get an SMS immediately. Then at the end of the month, that same transaction also appears on your HDFC credit card statement PDF. Now you have the same transaction twice in the system — once from SMS, once from the PDF.

The cross-source matcher solves this. It compares SMS transactions against CC statement transactions and looks for matches based on three criteria:

1. **Same amount** (exact match — Rs.1,234.50 must equal Rs.1,234.50)
2. **Same date** (within +/- 1 day, because the SMS date is when you made the purchase, but the CC statement date might be when the bank processed it, which can be a day later)
3. **Same card** (if both records have a card_last4, they must match)

When a match is found:

- The **CC statement transaction wins** (it is the authoritative record from the bank with the exact amount)
- The **SMS transaction gets marked as a duplicate** and linked to the CC record
- If the SMS had a better merchant name (SMS often says "SWIGGY" while the CC statement says "BUNDL TECHNOLOGIES PVT LTD"), the CC record gets enriched with the SMS merchant name

**Example:**

- SMS transaction: Rs.1,234.50 at "SWIGGY" on card x9434 on March 15
- CC statement transaction: Rs.1,234.50 from "BUNDL TECHNOLOGIES PVT LTD" on card x9434 on March 15
- Result: CC transaction kept, updated merchant to "Swiggy" (from alias), SMS marked as duplicate

### Date Normalization

Indian banks are incredibly inconsistent with date formats. The system encounters all of these:

| Bank / Source | Format                            | Example                           |
| ------------- | --------------------------------- | --------------------------------- |
| HDFC SMS      | YYYY-MM-DD or YYYY-MM-DD:HH:MM:SS | 2026-03-15 or 2026-03-15:14:30:00 |
| HDFC PDF      | DD/MM/YYYY                        | 15/03/2026                        |
| HSBC PDF      | DDMON (no year!)                  | 15MAR                             |
| SBI SMS       | DD/MM/YY                          | 15/03/26                          |
| ICICI PDF     | DD/MM/YYYY                        | 15/03/2026                        |
| Some SMS      | DD-MM                             | 15-03 (no year at all!)           |
| Generic       | DD-Mon-YYYY                       | 15-Mar-2026                       |

The date normalizer converts ALL of these to the standard `YYYY-MM-DD` format (2026-03-15). For formats missing the year (like HSBC's "15MAR"), it infers the year from the statement period or the current year. For two-digit years (like "26"), it adds 2000 if less than 50, or 1900 if 50 or more.

---

## 4. The Dashboard

The Cointrail dashboard is a dark-themed, data-dense web interface designed to give you instant answers about your money. It uses a deep dark color palette (#0a0a0f background, #12121a cards) with blue and purple gradients, Inter font for text, and JetBrains Mono for numbers. The design philosophy is: show a lot of data without being overwhelming.

### Expenses Page

The Expenses page is the central hub. It shows **every transaction from every source** in a single unified list. You see SMS transactions and CC statement transactions side by side, with the source indicated for each one.

**Features:**

- **Date range filter:** See transactions for today, this week, this month, or any custom date range
- **Category filter:** Show only Food & Dining, or only Shopping, or any other category
- **Bank filter:** Show only HDFC transactions, or only SBI, etc.
- **Card filter:** Show only transactions from a specific credit card (e.g., HDFC card ending in 9434)
- **Amount filter:** Find transactions above or below a certain amount
- **Search:** Free-text search across merchant names and descriptions
- **Manual entry:** You can also manually add a transaction (for cash payments or anything the system did not catch automatically)

Manual entries are stored in Cointrail's own local database (a lightweight SQLite file). Automated transactions come from the ingest service via API. The Expenses page merges both seamlessly — you cannot tell which is which unless you look at the source column.

### Credit Cards Page

This page is all about your credit card portfolio. It shows:

- **All 11 cards** listed with their bank, card name, last 4 digits, and network (Visa, MasterCard, etc.)
- **Per-card monthly spend:** For each card, the total amount spent in the current month, calculated live from actual transactions
- **Recent transactions:** The latest transactions on each card
- **Month-over-month trends:** How much you spent on each card this month compared to last month
- **Statements grouped by month:** All transactions from a specific card grouped into monthly statement buckets (January 2026, February 2026, etc.), showing transaction count and computed total for each statement period

### Spend Analysis Page

This page answers the big questions: "Where is my money going?"

- **Category-wise breakdown:** A chart showing how much you spent on Food & Dining vs Shopping vs Travel vs everything else. Each category shows its total amount and percentage of overall spend.
- **Monthly trends:** How your total spend changes month to month. Are you spending more in March than February? Which categories grew?
- **Top merchants:** Which merchants got the most of your money. Swiggy at the top? Amazon? Your fuel station?

All of this data is **real** — pulled directly from the ingest service's processed transaction database. There is no mock data, no sample data. Every number comes from an actual SMS or CC statement that flowed through the pipeline.

---

## 5. The FIRE Calculators

Cointrail includes six financial calculators, with a particular focus on FIRE (Financial Independence, Retire Early) planning. These are standalone tools — they do not connect to your expense data. They are pure calculators where you input assumptions and get projections.

### Calculator 1: Corpus Preservation Calculator (The Main FIRE Calculator)

This is the flagship calculator. The question it answers is: **"If I retire with X amount of money (my corpus), how long will it last?"**

But it does not use the simplistic "4% Safe Withdrawal Rate" (SWR) that American FIRE calculators use. The 4% rule says "withdraw 4% of your corpus every year and it should last 30 years." That model assumes your entire corpus is in stocks and bonds, which is not how most Indian investors (especially high-net-worth ones) think about money.

**The Yield-Based Model:**

Instead, Cointrail uses a **yield-based model**. The idea is:

- You do not eat into your corpus (the principal)
- You live off the **income** your corpus generates (rent from real estate, dividends from REITs, interest from gold bonds)
- Your corpus also **appreciates** over time (real estate prices go up, gold prices go up)
- The question becomes: "Does my corpus income cover my expenses, year after year, even as inflation pushes expenses up?"

**Asset Classes:**

The calculator lets you define your portfolio across five asset classes, each with its own yield and appreciation assumptions:

| Asset Class          | Default Allocation | Yield (Post-Tax) | Appreciation | Notes                                                                                                                                                     |
| -------------------- | ------------------ | ---------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Real Estate (Rental) | 40%                | 2.5%             | 6%           | Gross rental yield ~3.5%, net ~2.5% after 30% tax slab. Appreciation is unrealized (not taxed until you sell).                                            |
| Prime Agri Land      | 15%                | 1.5%             | 8%           | Agricultural income is completely tax-exempt under Section 10(1) of the Income Tax Act. Rural land capital gains are also exempt.                         |
| REITs                | 15%                | 4.5%             | 4%           | Real Estate Investment Trusts. Gross distribution ~6.5%, net ~4.5% after 30% slab. LTCG at 12.5% on NAV gains.                                            |
| InvITs               | 10%                | 5.5%             | 3%           | Infrastructure Investment Trusts. Toll roads, power grids, telecom towers. Gross ~8%, net ~5.5% after tax.                                                |
| Sovereign Gold Bonds | 20%                | 2.5%             | 8%           | RBI-issued bonds backed by gold. 2.5% guaranteed annual interest (taxed at slab) + gold price appreciation. LTCG is exempt if held to maturity (8 years). |

All yields shown are **post-tax at the 30% income tax slab** (since that is the applicable slab). This is crucial — a pre-tax yield of 6.5% on REITs becomes 4.5% after the government takes its cut.

You can adjust every slider: change the allocation percentages, change the yield assumptions, change the appreciation rates. The calculator blends them all into a single weighted portfolio yield and appreciation.

**How the Simulation Works:**

1. You enter your starting salary and expected salary growth (increments)
2. You define how many years you will work before retiring
3. The calculator simulates year by year:
   - Your salary grows by the increment percentage
   - Your corpus generates income (yield multiplied by corpus value)
   - If salary + corpus income exceeds your expenses, the surplus adds to the corpus
   - If not, the deficit eats into the corpus
   - Your corpus also appreciates each year
4. After your working years end, there is no more salary — you live purely off corpus income
5. The output shows three corpus targets: **Bare Minimum** (just barely survives), **Required** (comfortable buffer), and **Comfortable** (generous buffer)
6. A year-by-year table shows salary, corpus start, income generated, deficit/surplus, and corpus end for every year of the simulation

**Indian Number System:**

All amounts are shown in Lakhs and Crores, not millions and billions. Rs.1,00,000 is displayed as "Rs.1.00 L" and Rs.1,00,00,000 is "Rs.1.00 Cr". This is how Indians actually think about money.

### Calculator 2: Expense FIRE Calculator

While the main FIRE calculator works from a top-down salary perspective, the Expense FIRE calculator works **bottom-up from your actual monthly expenses**.

You enter your monthly spending in each category:

- Housing / Rent: Rs.35,000/month (default)
- Food & Dining: Rs.15,000/month
- Transport: Rs.8,000/month
- Shopping: Rs.10,000/month
- Bills & Utilities: Rs.5,000/month
- Health & Insurance: Rs.5,000/month
- Entertainment: Rs.3,000/month
- Travel: Rs.5,000/month
- Education: Rs.0/month
- EMI & Loans: Rs.0/month
- Miscellaneous: Rs.5,000/month

The critical insight here is that **different expense categories inflate at very different rates** in India:

| Category                                       | Annual Inflation Rate |
| ---------------------------------------------- | --------------------- |
| Healthcare                                     | 12%                   |
| Education                                      | 10%                   |
| Housing                                        | 7%                    |
| Everything else (Food, Shopping, Travel, etc.) | 6%                    |

Healthcare in India inflates at roughly double the general inflation rate. Education inflates even faster. Housing (especially in cities like Hyderabad, Bangalore, Mumbai) has its own trajectory. The calculator applies category-specific inflation rates to each expense line, so your projected expenses 20 years from now actually reflect the reality that your medical bills will grow much faster than your Netflix subscription.

The calculator then determines: given your expense projection and your portfolio allocation (same five asset classes as the main FIRE calculator), how large does your corpus need to be to generate enough income to cover all your expenses, forever?

It uses the same yield-based model, the same post-tax returns, and the same Indian number formatting. The key difference is that instead of a single "monthly expense" number, it models each category separately with its own inflation rate, giving a much more realistic picture of what early retirement actually costs in India.

### Calculator 3: SIP / Lumpsum Calculator

A standard Systematic Investment Plan (SIP) calculator. Enter:

- Monthly SIP amount (or a one-time lumpsum)
- Expected annual return rate
- Investment duration in years

It shows you how your investment grows over time, with a breakdown of how much is your invested principal vs how much is growth (returns). Useful for planning mutual fund SIPs or recurring deposits.

### Calculator 4: EMI Calculator

Calculates your Equated Monthly Installment for a loan. Enter:

- Loan amount
- Interest rate
- Loan tenure

It shows your monthly EMI, total interest paid, total amount paid, and an amortization schedule breaking down each month's payment into principal and interest portions. Useful for home loans, car loans, personal loans, or credit card EMI conversions.

### Calculator 5: Rent vs Buy Calculator

The perennial Indian question: should you rent a house or buy one? This calculator models both scenarios side by side:

- **Renting:** You pay rent (which increases every year), and you invest the difference between rent and what your EMI would have been
- **Buying:** You pay a down payment, take a home loan, pay EMI, pay maintenance, and own an appreciating asset

It factors in property appreciation, rental inflation, investment returns on the money you save by renting, tax benefits of home loan interest (Section 24b), and opportunity cost of the down payment. After the specified number of years, it tells you which option leaves you wealthier.

### Calculator 6: Tax Calculator — Old vs New Regime

India has two income tax regimes: the Old Regime (with deductions and exemptions like 80C, 80D, HRA) and the New Regime (lower tax rates but almost no deductions). For many people, especially those in the 30% slab, the decision of which regime to choose is not obvious.

This calculator lets you enter:

- Your gross annual income
- Your deductions (80C investments, 80D health insurance, HRA, home loan interest, NPS, etc.)

It then calculates your tax liability under both regimes side by side and tells you which one saves you more money. It accounts for the standard deduction, surcharge, cess, and all the quirks of Indian income tax.

---

## 6. Credit Card Management

### The Card Portfolio

As of March 2026, Cointrail tracks **11 credit cards** across **5 banks**:

| Bank  | Card / Last 4 | Notes                                                              |
| ----- | ------------- | ------------------------------------------------------------------ |
| HDFC  | xx9434        | Primary HDFC card, highest transaction volume (141 txns in system) |
| HDFC  | xx8154        | Secondary HDFC card                                                |
| HDFC  | xx4956        |                                                                    |
| HDFC  | xx7180        |                                                                    |
| HSBC  | xx2006        | HSBC India card                                                    |
| Axis  | xx1234        | Axis Airtel credit card                                            |
| SBI   | xx6792        | SBI Card                                                           |
| SBI   | xx2111        | SBI Card                                                           |
| SBI   | xx2971        | SBI Card                                                           |
| ICICI | xx0026        | ICICI Amazon Pay credit card                                       |
| ICICI | xx1019        | ICICI Sapphiro credit card                                         |

### Per-Card Statement Tracking

The Credit Cards page shows statements grouped by month for each card. When you click on a card, you see something like:

- **January 2026:** 35 transactions, computed total Rs.42,315
- **February 2026:** 41 transactions, computed total Rs.38,720
- **March 2026:** 28 transactions (month in progress), computed total Rs.31,105

These statement groups are computed from actual parsed transaction data — not from a predefined statement cycle. The system groups all transactions for a card by their month and calculates the total.

### The ICICI Sapphiro Anomaly

There is an interesting edge case with ICICI credit cards. ICICI sometimes issues **two cards on the same billing account** — in this case, the Amazon Pay card (xx0026) and the Sapphiro card (xx1019). They share a single monthly statement PDF. This means when the ICICI PDF parser processes a statement, all transactions in it might belong to either card.

The system currently assigns all ICICI transactions based on the card_last4 that appears in the individual transaction lines within the PDF. If the PDF does not specify which card a transaction belongs to, it defaults to one card (usually xx0026). Future improvements may use additional heuristics (like merchant type or amount patterns) to split them more accurately.

### Credit Card Data Coverage

| Card   | Bank  | Transactions in System | Date Range          | Approximate Total |
| ------ | ----- | ---------------------- | ------------------- | ----------------- |
| xx9434 | HDFC  | 141                    | Dec 2025 - Mar 2026 | Rs.1,65,221       |
| xx0026 | ICICI | 28                     | Dec 2025 - Feb 2026 | Rs.2,00,835       |
| xx1234 | Axis  | 10                     | Dec 2025 - Mar 2026 | Rs.13,224         |
| xx2006 | HSBC  | 4                      | Jan 2026 - Feb 2026 | Rs.10,536         |

The remaining cards have SMS transaction data but no CC statement data yet (statements have not been processed or PDFs are still locked with unknown passwords).

---

## 7. Architecture (How It All Fits Together)

### The Two Services

Cointrail is not a single application. It is two services working together:

**1. Cointrail (The Frontend)**

- **What it is:** A Next.js 15 web application with Tailwind CSS 4 for styling
- **What it does:** Renders the dashboard, the calculator pages, handles the user interface
- **What it stores:** Only manual expense entries (in a local SQLite database)
- **What it queries:** Everything else comes from the Ingest service via API
- **Port:** 3000 (internally), mapped to 8501 on the host
- **URL:** https://cointrail.example.com

**2. Ingest (The Data Pipeline)**

- **What it is:** A Python FastAPI application
- **What it does:** Receives SMS, syncs Gmail, downloads/decrypts/parses CC statement PDFs, categorizes transactions, deduplicates across sources, serves data via REST API
- **What it stores:** Everything — raw SMS, emails, parsed transactions, credit cards, merchant aliases (all in SQLite)
- **Port:** 8600
- **URL:** https://ingest.example.com

### How They Talk to Each Other

Both services run as Docker containers on the same physical server (your server). They are connected through a Docker network called `projects-net`. This means Cointrail can reach the Ingest service at `http://ingest:8600` (using Docker's internal DNS), without going through the internet.

Cointrail has a helper module (`lib/ingest.ts`) that provides a centralized `ingestFetch()` function for all API calls to the Ingest service. All 8 API routes in Cointrail query the Ingest service:

- `/api/expenses` — merges manual entries from local SQLite with automated transactions from Ingest
- `/api/cards` — queries Ingest for the card list, calculates per-card monthly spend live
- `/api/cards/statements` — groups card transactions by month into statement objects
- `/api/cards/sync-gmail` — triggers Ingest email sync
- (iter-18 CH4) `/api/analysis` and the `/api/sms-ingest`, `/api/sms-sync`, `/api/sms-parse` proxies were removed: no UI caller, and the SMS proxies let any LAN client inject SMS into Ingest

### The Complete Data Flow

Here is how a single credit card swipe travels through the entire system:

```
Step 1:  You swipe your HDFC card at Swiggy for Rs.1,234
Step 2:  HDFC sends an SMS to your phone within seconds
Step 3:  Tasker on your phone catches the SMS and forwards it to ingest.example.com/sms
Step 4:  Ingest stores the raw SMS in the sms table
Step 5:  The SMS parser matches it against regex patterns, extracts amount/card/merchant/date
Step 6:  A new transaction record is created in the transactions table (source: "sms")
Step 7:  The categorizer checks "SWIGGY" against the alias table — match found: Food & Dining
Step 8:  Transaction is saved with category="Food & Dining", confidence="high"

         ...One month later...

Step 9:  HDFC emails you the monthly CC statement PDF
Step 10: Gmail sync fetches the new email, detects it is from hdfcbank.net, downloads the PDF
Step 11: PDF is encrypted — system tries known passwords, finds one that works, decrypts
Step 12: Bank detector reads the first page: "HDFC BANK" — routes to HDFC parser
Step 13: HDFC parser reads every transaction line, including "BUNDL TECHNOLOGIES: 1,234.50"
Step 14: New transaction created (source: "card")
Step 15: Categorizer sees "BUNDL TECHNOLOGIES" — alias match: Swiggy, Food & Dining
Step 16: Cross-matcher runs: finds the SMS txn from Step 6, same amount, same card, same date
Step 17: SMS transaction marked as duplicate, CC transaction enriched with better merchant name
Step 18: You open the Cointrail dashboard at cointrail.example.com
Step 19: Cointrail's Expenses API calls ingest GET /transactions
Step 20: The single, deduplicated, categorized transaction appears on your screen
```

### The Home Server (your server)

All of this runs on a single home server. The server runs Docker containers for multiple projects, including Cointrail and Ingest. It is accessible over the local network and also via Cloudflare Tunnel for secure external access.

Key infrastructure:

- **Docker:** All services run as containers, managed with docker-compose
- **Gitea:** Self-hosted Git server at git.example.com for version control
- **NPM (Nginx Proxy Manager):** Reverse proxy that routes subdomains to the correct Docker containers (cointrail.example.com goes to port 8501, ingest.example.com goes to port 8600)
- **Cloudflare Tunnel:** Provides HTTPS and external access without exposing the server's IP address

### Database Choice: SQLite

Both Cointrail and Ingest use SQLite, not PostgreSQL or MySQL. Why?

- **Simplicity:** SQLite is a single file. No database server to maintain, no connection strings, no authentication.
- **Single user:** Cointrail is a single-user application. SQLite handles single-user workloads perfectly.
- **Performance:** For 1,000-2,000 transactions, SQLite is more than fast enough. You would need tens of millions of rows before SQLite becomes a bottleneck.
- **Portability:** The entire database is one file. You can back it up by copying one file. You can move it to another server by copying one file.
- **WAL mode:** Both databases use SQLite's Write-Ahead Logging mode, which allows concurrent reads while a write is happening. This means the dashboard can read data while the Ingest service is simultaneously writing new transactions.

### Ingest Database Schema

The Ingest service stores everything in these tables:

- **sms:** Raw SMS storage — sender, body, phone timestamp, whether it is financial, whether it has been processed
- **transactions:** Parsed transactions — amount, date, description, category, source (sms/card), card_last4, bank, confidence, transaction type (debit/credit), raw description, dedup flags
- **cards:** Credit card records — bank name, card name, last 4 digits
- **emails:** Raw email storage — Gmail ID, subject, sender, date, body preview, whether it is financial, whether it has an attachment
- **merchant_aliases:** The alias lookup table — pattern text, match type (contains/startswith/regex), merchant name, category, source (seed/user_override)

---

## 8. Data Security and Privacy

### Everything Runs Locally

This is the single most important security feature: **nothing is hosted in the cloud**. No AWS, no Google Cloud, no Heroku. Every piece of data — your SMS messages, your email metadata, your credit card statements, your transaction history — lives on a physical server in your home.

### No Data Leaves the Network

When Cointrail queries the Ingest service, that API call travels over a Docker internal network. It does not go through the internet. When you access the dashboard from your phone or laptop, the data travels over your home WiFi or through the Cloudflare tunnel (which is encrypted end-to-end).

### No Bank Credentials Shared

Cointrail never asks for your bank login credentials. It does not use bank APIs that require your password. It reads data that your bank already pushes to you:

- SMS messages that arrive on your phone
- Emails that arrive in your Gmail
- PDF statements that your bank attaches to those emails

### Gmail Access

The system uses Google's OAuth2 for Gmail access. This means:

- You authorize it once by logging into your Google account and clicking "Allow"
- Google gives the system a token (not your password)
- The token only grants access to read emails and attachments — it cannot send emails, delete emails, or access anything else in your Google account
- You can revoke this access at any time from your Google account security settings

### CC Statement Passwords

Credit card statement PDF passwords are injected as per-bank environment variables (`PDF_PASSWORD_HDFC`, `PDF_PASSWORD_ICICI`, `PDF_PASSWORD_HSBC_CC`, `PDF_PASSWORD_HSBC_ACCOUNT`, `PDF_PASSWORD_SBI`, `PDF_PASSWORD_AXIS`) on the Ingest container. Cointrail itself stores no plaintext credentials. The passwords themselves are typically your date of birth or PAN number — information that your bank already has.

### No Telemetry, No Analytics

Cointrail sends no usage data, no analytics, no telemetry to any third party. There are no tracking pixels, no Google Analytics, no Mixpanel. It is completely dark to the outside world.

---

## 9. How to Add a New Credit Card

When you get a new credit card, here is what needs to happen to start tracking it in Cointrail:

### Step 1: The Card Will Auto-Appear from SMS

If your bank sends SMS alerts for the new card (which almost all Indian banks do), and your phone has Tasker set up, the first time you use the card and receive an SMS, the Ingest service will:

1. Receive the SMS
2. Parse it (if the bank format is supported — HDFC, HSBC, SBI, ICICI, Axis, and others are)
3. Automatically create a new card record in the database with the card's last 4 digits and bank name
4. Start recording transactions

**You do not need to do anything for this step.** It just works.

### Step 2: Add the PDF Password

When the first CC statement PDF arrives (usually at the end of the first billing cycle), you need to add its password so the system can decrypt it.

1. SSH into the server
2. Edit the passwords file inside the Ingest container's data directory
3. Add the password on a new line (usually your date of birth in DDMMYYYY format, or your PAN number, or whatever the bank told you the password is)
4. Save and exit

Next time the PDF processor runs, it will try all passwords against all locked PDFs and decrypt anything it can.

### Step 3: Verify the Bank Parser Exists

Check if the Ingest service has a parser for the new card's bank. Currently supported: HDFC, HSBC, ICICI, SBI, Axis. The bank detector can identify 16 different banks, but parsers only exist for the five listed above.

If your new card is from a different bank (say Kotak or Amex), a new parser would need to be written. This involves:

- Looking at the bank's PDF statement layout
- Understanding how dates, amounts, and descriptions are formatted
- Writing regex patterns to extract each transaction line
- Adding the parser to the Ingest service's parsers directory

### Step 4: Check the Dashboard

After a few transactions and (optionally) the first statement PDF being processed, go to the Credit Cards page on the dashboard. Your new card should appear in the list with its recent transactions and monthly spend.

---

## 10. How to Fix Miscategorized Transactions

No categorization system is perfect. Sometimes a transaction will end up as "Uncategorized" or in the wrong category. Here is how to fix it.

### The Recategorize Endpoint

Every transaction displayed on the dashboard has a unique ID. The Ingest service provides a `PUT /transactions/:id` endpoint that lets you change a transaction's merchant name and category.

For example, if a transaction shows up as:

- Description: "SOME RANDOM MERCHANT LTD"
- Category: "Uncategorized"

You can update it to:

- Merchant: "Corner Cafe"
- Category: "Food & Dining"

### How Alias Learning Works

Here is the clever part: when you recategorize a transaction, the system does not just fix that one transaction. It **learns from your correction**.

The `learn_from_override` function takes the raw description of the transaction you corrected and creates a new entry in the merchant alias table. So if you corrected "CORNER CAFE AND BAKERY HYD" to be "Corner Cafe" in "Food & Dining", the system creates a new alias:

- Pattern: "corner cafe and bakery hyd" (first 40 characters, lowercased)
- Match type: "contains"
- Merchant name: "Corner Cafe"
- Category: "Food & Dining"
- Source: "user_override"

Now, the next time any transaction from "CORNER CAFE AND BAKERY HYD" comes through — whether from SMS or a future CC statement — it will automatically be categorized as "Corner Cafe" in "Food & Dining" with high confidence. You only need to correct it once.

This means the categorization system gets smarter over time. Every correction you make teaches it a new merchant. The 110+ seed aliases that came with the system handle the major brands, and your overrides fill in the local restaurants, specific doctors, neighborhood shops, and other merchants that are unique to your spending.

---

## 11. What is Automated vs What Needs Manual Work

### Fully Automated (Happens Without You Doing Anything)

- **SMS capture:** Every bank SMS is automatically forwarded from your phone to the ingest service via Tasker, parsed, categorized, and stored. You swipe a card, the transaction appears on your dashboard within seconds.
- **Email sync:** Gmail is checked for new emails. Financial emails are classified automatically.
- **PDF parsing:** When CC statement PDFs are available and the password is known, they are automatically decrypted, parsed, and every transaction is extracted and categorized.
- **Categorization:** All transactions go through the two-pass categorizer automatically. Known merchants (110+ in the alias table) get high-confidence categories. Unknown merchants get keyword-based categories or "Uncategorized."
- **Cross-source matching:** SMS and CC statement transactions are automatically deduplicated.
- **Card auto-creation:** New credit cards are automatically registered when the first transaction from an unknown card_last4 is processed.
- **Alias learning:** When you correct a miscategorized transaction, the system learns the new alias and applies it to all future transactions automatically.

### Semi-Automated (Happens Automatically After One-Time Setup)

- **Phone Tasker URL:** You need to set up Tasker on your Android phone once, pointing it to `ingest.example.com/sms`. After that, it runs forever in the background. If you change phones, you need to set it up again on the new phone.
- **Gmail OAuth:** You need to authorize Gmail access once. After that, the token refreshes automatically. It only needs re-authorization if the token expires (rare) or if you revoke access.
- **Hourly email sync cron:** Needs to be configured once as a scheduled task on the server. (Note: as of March 2026, this is being set up — sync can also be triggered manually via the dashboard's "Sync Gmail" button.)

### Manual Work Required

- **New card PDF passwords:** When you get a new credit card, you need to add its statement PDF password to the passwords file on the server. This is a one-time task per card.
- **New bank parser:** If you get a card from a bank that does not have a parser yet (anything other than HDFC, HSBC, ICICI, SBI, Axis), someone needs to write a new parser. This requires looking at the bank's PDF format and writing regex patterns to extract transactions.
- **Correcting miscategorized transactions:** About 40% of transactions are still uncategorized (especially UPI person-to-person transfers and bank charges like annual fees, late fees, GST). These need manual recategorization, though each correction teaches the system for next time. This percentage will decrease over time as more aliases are learned.
- **Locked PDFs with unknown passwords:** There are currently 12 PDF statements that the system cannot decrypt because the password is not in the known passwords list. These need to be identified (which bank, which card) and their passwords added manually.
- **Manual expense entry:** Cash transactions, payments through methods that do not generate SMS (rare in India but possible), or transactions from banks without SMS parsing support need to be entered manually through the Expenses page.
- **Server maintenance:** Occasionally, Docker containers need to be rebuilt after code updates. This is done via SSH with a docker compose command.
- **Bulk SMS import:** If you want to backfill historical data, you can export all SMS from your phone using the "SMS Backup and Restore" Android app (which creates an XML file), upload the XML to the server, and trigger the bulk import endpoint. The Ingest service has a streaming XML parser that can handle files over 200MB with thousands of SMS messages. This is a one-time task if you want historical data.

---

_Cointrail is an ongoing project. Features are being added, parsers are being improved, and coverage is growing. The goal is to reach a state where every single rupee flowing through every financial account is automatically tracked, categorized, and visible on one dashboard — with zero manual effort._
