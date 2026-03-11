"""
backfill_history.py — Seeds trade_history.json with 365 days of past Form 4 data.

Run this ONCE before going live. No Claude API calls — just SEC metadata.
Takes 20-30 minutes. Safe to re-run (merges with existing history).

Usage:
    python3 backfill_history.py

What it does:
    - Fetches Form 4 filings from SEC EDGAR going back 365 days
    - Extracts insider name, ticker, transaction type, and date from XML
    - Saves to data/trade_history.json in the same format the bot uses
    - Does NOT score or post anything — purely history seeding

After running:
    - "first trade in 12+ months" bonus will fire correctly
    - "consecutive buys" bonus will fire correctly
    - Scoring will be meaningfully higher for unusual activity
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── Load .env ────────────────────────────────────────────────────────────────
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

HEADERS = {
    "User-Agent": "Form4Wire support@form4wire.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,*/*",
}

TRADE_HISTORY_FILE = "data/trade_history.json"
BATCH_SIZE         = 100
PAGES_PER_WEEK     = 4      # 400 filings per week window
LOOKBACK_DAYS      = 365
SLEEP_BETWEEN_PAGES = 0.5   # Polite to SEC
SLEEP_BETWEEN_WEEKS = 1.0

# Tier 1 titles — only backfill meaningful insiders to keep file size manageable
TIER1_TITLES = [
    "chief executive", "ceo", "chief financial", "cfo",
    "chief operating", "coo", "chairman", "president",
    "general counsel", "chief legal", "clo",
    "chief technology", "cto", "chief revenue", "cro",
    "executive vice president", "evp", "senior vice president", "svp",
    "vice president", "vp", "director",
]

SKIP_CODES = {"F", "A", "G", "D"}  # Same filters as live bot


def load_history():
    if os.path.exists(TRADE_HISTORY_FILE):
        with open(TRADE_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history):
    os.makedirs(os.path.dirname(TRADE_HISTORY_FILE), exist_ok=True)
    with open(TRADE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def get_feed_url(start_date, end_date, offset=0):
    return (
        f"https://efts.sec.gov/LATEST/search-index?q=%22%22&forms=4"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
        f"&_source=file_date,period_ending,ciks,display_names,adsh"
        f"&from={offset}&size={BATCH_SIZE}"
    )


def fetch_xml(url):
    """Fetch filing XML — returns raw text or None."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def parse_xml_lightweight(xml_text):
    """
    Extract just what we need from Form 4 XML without Claude.
    Returns dict with: insider_name, insider_title, ticker, transaction_code,
                       transaction_date, shares, price, total_value
    or None if can't parse.
    """
    if not xml_text:
        return None

    def extract(tag, text):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    try:
        insider_name  = extract("rptOwnerName", xml_text)
        insider_title = extract("officerTitle", xml_text) or extract("relationship", xml_text)
        ticker        = extract("issuerTradingSymbol", xml_text)

        if not insider_name or not ticker:
            return None

        # Check tier — skip if not meaningful
        title_lower = insider_title.lower()
        if not any(t in title_lower for t in TIER1_TITLES):
            return None

        # Find all non-derivative transactions
        tx_blocks = re.findall(
            r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
            xml_text, re.DOTALL | re.IGNORECASE
        )

        trades = []
        for block in tx_blocks:
            code = extract("transactionCode", block)
            if not code or code in SKIP_CODES:
                continue

            date_str = extract("transactionDate", block)
            date_val = extract("value", date_str) if "<value>" in date_str else date_str

            shares_str = extract("transactionShares", block)
            shares_val = extract("value", shares_str) if "<value>" in shares_str else shares_str

            price_str  = extract("transactionPricePerShare", block)
            price_val  = extract("value", price_str) if "<value>" in price_str else price_str

            try:
                shares = float(re.sub(r"[^0-9.]", "", shares_val)) if shares_val else 0
                price  = float(re.sub(r"[^0-9.]", "", price_val))  if price_val  else 0
                total  = shares * price
            except Exception:
                shares, price, total = 0, 0, 0

            if total > 0 or code in ("P", "S"):
                trades.append({
                    "insider_name":    insider_name,
                    "insider_title":   insider_title,
                    "ticker":          ticker.upper(),
                    "transaction_code": code,
                    "transaction_date": date_val,
                    "shares_traded":   shares,
                    "price_per_share": price,
                    "total_value":     total,
                    "is_buy":          code == "P",
                })

        return trades if trades else None

    except Exception:
        return None


def record_trades(history, trades):
    """Add trades to history dict. Returns count of new records added."""
    added = 0
    for trade in trades:
        ticker  = trade.get("ticker", "UNKNOWN")
        insider = trade.get("insider_name", "Unknown")
        key     = f"{ticker}:{insider}"

        if key not in history:
            history[key] = []

        entry = {
            "date":            trade.get("transaction_date", ""),
            "code":            trade.get("transaction_code", ""),
            "is_buy":          trade.get("is_buy", False),
            "total_value":     trade.get("total_value", 0),
            "price_per_share": trade.get("price_per_share", 0),
            "shares":          trade.get("shares_traded", 0),
            "saved_at":        datetime.now(timezone.utc).isoformat(),
            "source":          "backfill",
        }

        # Avoid exact date duplicates
        existing_dates = {e.get("date") for e in history[key]}
        if entry["date"] not in existing_dates:
            history[key].append(entry)
            added += 1

        # Keep last 20 trades per insider
        history[key] = sorted(history[key], key=lambda x: x.get("date", ""), reverse=True)[:20]

    return added


def fetch_week(start_date, end_date):
    """Fetch all filings for a date range and return list of (url, accession) tuples."""
    filing_urls = []
    for page in range(PAGES_PER_WEEK):
        offset = page * BATCH_SIZE
        url    = get_feed_url(start_date, end_date, offset)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    ⚠️  Feed error: {e}")
            break

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            source    = hit.get("_source", {})
            filing_id = hit.get("_id", "")
            ciks      = source.get("ciks", [""])
            cik       = ciks[0] if ciks else ""
            accession = filing_id.split(":")[0] if ":" in filing_id else filing_id
            acc_clean = accession.replace("-", "")

            if cik and accession:
                xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}.txt"
                filing_urls.append(xml_url)

        time.sleep(SLEEP_BETWEEN_PAGES)

    return filing_urls


def main():
    print("=" * 60)
    print("FORM4WIRE — 365-DAY HISTORY BACKFILL")
    print("=" * 60)
    print("This seeds trade_history.json with past insider trades.")
    print("No Claude API calls. Safe to re-run.\n")

    history       = load_history()
    existing_keys = len(history)
    total_added   = 0
    total_filings = 0
    total_parsed  = 0

    today    = datetime.utcnow().date()
    # Process in 2-week windows going back 365 days
    windows  = []
    end      = today
    while (today - end).days < LOOKBACK_DAYS:
        start = end - timedelta(days=13)  # 2-week window
        if (today - start).days > LOOKBACK_DAYS:
            start = today - timedelta(days=LOOKBACK_DAYS)
        windows.append((str(start), str(end)))
        end = start - timedelta(days=1)

    print(f"📅 Processing {len(windows)} two-week windows back to {today - timedelta(days=LOOKBACK_DAYS)}")
    print(f"📂 Existing history keys: {existing_keys}\n")

    for i, (start, end) in enumerate(windows):
        print(f"[{i+1}/{len(windows)}] {start} → {end}", end=" ... ", flush=True)

        filing_urls = fetch_week(start, end)
        total_filings += len(filing_urls)

        week_added  = 0
        week_parsed = 0

        for url in filing_urls:
            xml = fetch_xml(url)
            if not xml:
                # Try alternate URL format
                xml = fetch_xml(url.replace(".txt", "-index.htm"))

            if xml:
                trades = parse_xml_lightweight(xml)
                if trades:
                    week_parsed += 1
                    added = record_trades(history, trades)
                    week_added += added

            time.sleep(0.15)  # ~7 requests/sec — well within SEC limits

        total_added  += week_added
        total_parsed += week_parsed
        print(f"{len(filing_urls)} filings, {week_parsed} parsed, {week_added} records added")

        # Save progress after each window in case of interruption
        save_history(history)
        time.sleep(SLEEP_BETWEEN_WEEKS)

    # Final save
    save_history(history)

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"✅ Total filings fetched:   {total_filings:,}")
    print(f"✅ Filings parsed:          {total_parsed:,}")
    print(f"✅ History records added:   {total_added:,}")
    print(f"✅ Unique insider keys:     {len(history):,}")
    print(f"\n📂 Saved to: {TRADE_HISTORY_FILE}")
    print("\nYou're ready. Run python3 test_mode.py 400 to test scoring.")


if __name__ == "__main__":
    main()
