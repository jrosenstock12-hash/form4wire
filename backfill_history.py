"""
backfill_history.py — Seeds trade_history.json with 365 days of past Form 4 data.

Run this ONCE (or re-run to refresh). No Claude API calls — just SEC data.
Takes 20-30 minutes. Safe to re-run (merges with existing history).

Usage:
    python3 backfill_history.py

What it does:
    - Fetches ALL Form 4 filings from SEC EDGAR going back 365 days
    - Captures ALL code P (open market purchase) transactions
    - No title/role filtering — captures every insider regardless of level
    - Saves to data/trade_history.json in the same format the bot uses
    - Does NOT score or post anything — purely history seeding

After running:
    - "first trade in 12+ months" bonus will fire correctly
    - Cluster detection will be accurate
    - Scoring will reflect true insider activity history
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

TRADE_HISTORY_FILE  = "data/trade_history.json"
BATCH_SIZE          = 100
PAGES_PER_WINDOW    = 20      # 2,000 filings per window
LOOKBACK_DAYS       = 365
SLEEP_BETWEEN_PAGES = 0.5     # Polite to SEC
SLEEP_BETWEEN_WEEKS = 1.0
SLEEP_PER_FILING    = 0.15    # ~7 requests/sec — within SEC limits
MAX_TRADES_PER_KEY  = 50      # Keep last 50 trades per insider (up from 20)


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


def extract_tag(tag, text):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def get_value(block):
    """Extract <value>...</value> from a block, or return the block itself."""
    m = re.search(r"<value>(.*?)</value>", block, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else block.strip()


def parse_xml(xml_text):
    """
    Extract all code P transactions from Form 4 XML.
    No title filtering — captures every insider.
    Returns list of trade dicts or None.
    """
    if not xml_text or len(xml_text) < 200:
        return None

    try:
        insider_name  = extract_tag("rptOwnerName", xml_text)
        insider_title = extract_tag("officerTitle", xml_text)
        if not insider_title:
            # Try relationship block
            rel_block = extract_tag("reportingOwnerRelationship", xml_text)
            insider_title = extract_tag("officerTitle", rel_block) or ""

        ticker = extract_tag("issuerTradingSymbol", xml_text)

        if not insider_name or not ticker:
            return None

        ticker = ticker.upper().strip()

        # Find all non-derivative transactions
        tx_blocks = re.findall(
            r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
            xml_text, re.DOTALL | re.IGNORECASE
        )

        trades = []
        for block in tx_blocks:
            code_block = extract_tag("transactionCode", block)
            code = get_value(code_block) if code_block else ""

            # Only capture open market purchases (P)
            if code != "P":
                continue

            date_block = extract_tag("transactionDate", block)
            date_val   = get_value(date_block) if date_block else ""

            shares_block = extract_tag("transactionShares", block)
            shares_val   = get_value(shares_block) if shares_block else ""

            price_block  = extract_tag("transactionPricePerShare", block)
            price_val    = get_value(price_block) if price_block else ""

            owned_block  = extract_tag("sharesOwnedFollowingTransaction", block)
            owned_val    = get_value(owned_block) if owned_block else ""

            try:
                shares = float(re.sub(r"[^0-9.]", "", shares_val)) if shares_val else 0
                price  = float(re.sub(r"[^0-9.]", "", price_val))  if price_val  else 0
                owned  = float(re.sub(r"[^0-9.]", "", owned_val))  if owned_val  else 0
                total  = shares * price
            except Exception:
                shares, price, owned, total = 0, 0, 0, 0

            if not date_val:
                continue

            trades.append({
                "insider_name":    insider_name,
                "insider_title":   insider_title,
                "ticker":          ticker,
                "transaction_code": "P",
                "transaction_date": date_val,
                "shares_traded":   shares,
                "price_per_share": price,
                "total_value":     total,
                "shares_owned":    owned,
                "is_buy":          True,
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
            "code":            "P",
            "is_buy":          True,
            "total_value":     trade.get("total_value", 0),
            "price_per_share": trade.get("price_per_share", 0),
            "shares":          trade.get("shares_traded", 0),
            "shares_owned":    trade.get("shares_owned", 0),
            "title":           trade.get("insider_title", ""),
            "saved_at":        datetime.now(timezone.utc).isoformat(),
            "source":          "backfill",
        }

        # Avoid exact date duplicates for same insider
        existing_dates = {e.get("date") for e in history[key]}
        if entry["date"] not in existing_dates:
            history[key].append(entry)
            added += 1

        # Keep last MAX_TRADES_PER_KEY trades, most recent first
        history[key] = sorted(
            history[key], key=lambda x: x.get("date", ""), reverse=True
        )[:MAX_TRADES_PER_KEY]

    return added


def fetch_filing_urls(start_date, end_date):
    """Fetch all filing URLs for a date range."""
    filing_urls = []
    for page in range(PAGES_PER_WINDOW):
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
                xml_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{acc_clean}/{accession}.txt"
                )
                filing_urls.append(xml_url)

        time.sleep(SLEEP_BETWEEN_PAGES)

    return filing_urls


def main():
    print("=" * 60)
    print("FORM4WIRE — 365-DAY HISTORY BACKFILL (ALL CODE P BUYS)")
    print("=" * 60)
    print("Captures ALL open market purchases regardless of role.")
    print("No Claude API calls. Safe to re-run.\n")

    history       = load_history()
    existing_keys = len(history)
    total_added   = 0
    total_filings = 0
    total_parsed  = 0

    today   = datetime.utcnow().date()
    windows = []
    end     = today
    while (today - end).days < LOOKBACK_DAYS:
        start = end - timedelta(days=1)  # 2-day window
        if (today - start).days > LOOKBACK_DAYS:
            start = today - timedelta(days=LOOKBACK_DAYS)
        windows.append((str(start), str(end)))
        end = start - timedelta(days=1)

    print(f"📅 Processing {len(windows)} two-week windows")
    print(f"📅 Date range: {today - timedelta(days=LOOKBACK_DAYS)} → {today}")
    print(f"📂 Existing history keys: {existing_keys}\n")

    for i, (start, end) in enumerate(windows):
        print(f"[{i+1}/{len(windows)}] {start} → {end}", end=" ... ", flush=True)

        filing_urls   = fetch_filing_urls(start, end)
        total_filings += len(filing_urls)

        week_added  = 0
        week_parsed = 0

        for url in filing_urls:
            xml = fetch_xml(url)
            if not xml:
                xml = fetch_xml(url.replace(".txt", "-index.htm"))

            if xml:
                trades = parse_xml(xml)
                if trades:
                    week_parsed += 1
                    added = record_trades(history, trades)
                    week_added += added

            time.sleep(SLEEP_PER_FILING)

        total_added  += week_added
        total_parsed += week_parsed
        print(f"{len(filing_urls)} filings, {week_parsed} w/buys, {week_added} records added")

        # Save after each window in case of interruption
        save_history(history)
        time.sleep(SLEEP_BETWEEN_WEEKS)

    save_history(history)

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"✅ Total filings fetched:   {total_filings:,}")
    print(f"✅ Filings with P buys:     {total_parsed:,}")
    print(f"✅ History records added:   {total_added:,}")
    print(f"✅ Unique insider keys:     {len(history):,}")
    print(f"\n📂 Saved to: {TRADE_HISTORY_FILE}")
    print("\nRe-run bot — 'first insider buy in 12+ months' will now be accurate.")


if __name__ == "__main__":
    main()
