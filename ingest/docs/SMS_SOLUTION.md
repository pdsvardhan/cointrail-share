# SMS Solution — Research & Recommendation

**Date:** 2026-04-02
**Status:** Recommendation ready, implementation pending phone-side setup

---

## Problem

The SMS Forwarder app conflicts with banking/RSA WorkAuthenticator apps due to Android security restrictions. When SMS Forwarder is set as the default messaging app (required for some forwarding methods), banking apps detect it as a non-standard SMS handler and refuse to run properly.

## Options Evaluated

### 1. MacroDroid (RECOMMENDED)
- **How:** MacroDroid intercepts SMS via "SMS Received" trigger, filters by sender, and POSTs JSON payload to `ingest.example.com/sms`
- **Pros:**
  - Free tier allows 5 macros (we only need 1)
  - Does NOT require being the default SMS app
  - Does NOT interfere with banking/RSA apps
  - Simple UI, easy to set up
  - Can run alongside any SMS app
- **Cons:**
  - Free version has ads (non-intrusive, only in the app UI)
  - Free tier limited to 5 macros (sufficient for this use case)

### 2. Tasker
- **How:** Same approach as MacroDroid but with more power
- **Pros:** Most reliable, most configurable
- **Cons:** Paid ($3.50 one-time)
- **Verdict:** Overkill for this use case, upgrade later if needed

### 3. Periodic XML Backup Sync
- **How:** SMS Backup & Restore app auto-exports XML. Import via `/sms/import/xml`
- **Pros:** Zero phone-side interference
- **Cons:** Not real-time (hours delay), more complex
- **Verdict:** Good as monthly fallback

### 4. ADB over WiFi
- **Verdict:** Too fragile for production use

---

## Setup: MacroDroid

### Step 1: Install
- Install MacroDroid from Play Store (free)

### Step 2: Create the Macro

**Trigger:**
1. Open MacroDroid > Add Macro
2. Add Trigger > Device Events > SMS Received
3. Set "Content/Sender" filter: Select "Sender matches"
4. Add sender patterns (one per line):
   - `HDFC`
   - `ICICI`
   - `SBI`
   - `HSBC`
   - `AXIS`
   - `Swiggy`
   - `Pluxee`
   - `PhonePe`
   - `GPay`
   - `KOTAK`
   - `CRED`

**Action:**
1. Add Action > Connectivity > HTTP Request
2. Configure:
   - **Method:** POST
   - **URL:** `https://ingest.example.com/sms`
   - **Content Type:** application/json
   - **Header:** `X-API-Key` = `<your API key from .env>`
   - **Body:**
     ```
     {"sender": "{sms_number}", "body": "{sms_text}", "timestamp": "{device_time_millis}"}
     ```
3. MacroDroid variables for SMS:
   - `{sms_number}` = sender number/name
   - `{sms_text}` = SMS body
   - `{device_time_millis}` = timestamp

**Name:** "Forward Financial SMS to Ingest"

### Step 3: Permissions & Battery
1. Grant SMS permission when prompted
2. Go to Android Settings > Apps > MacroDroid > Battery > Unrestricted
3. Also: Settings > Battery > Battery Optimization > MacroDroid > Don't Optimize

### Step 4: Test
1. Ask someone to send you an SMS, or wait for a bank SMS
2. Check: `curl -H "X-API-Key: <key>" https://ingest.example.com/stats`
3. Verify the SMS count increased

---

## Fallback: Monthly XML Sync

For catching any SMS that MacroDroid might miss:
1. Install "SMS Backup & Restore" app (free)
2. Export SMS to XML file
3. Upload to server: `scp backup.xml user@your-server:/data/sms_imports/`
4. Import: `curl -X POST -H "X-API-Key: <key>" -d '{"path":"/data/sms_imports/backup.xml"}' https://ingest.example.com/sms/import/xml`

---

## Current State

The Ingest service already supports both methods:
- `POST /sms` — receives real-time SMS (with API key auth)
- `POST /sms/import/xml` — bulk imports from XML backup

## Action Items (User)
1. Install MacroDroid and create the macro (instructions above)
2. Test with one SMS to verify end-to-end flow
3. Monitor for 48 hours to confirm reliability
4. Run one XML backup import to catch any historical gap
