"""
backfill_gaps.py — Fills in specific date ranges that failed during the main backfill.

Usage:
    python3 backfill_gaps.py
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone

HEADERS = {
    "User-Agent": "Form4Wire support@form4wire.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,*/*",
}

TRADE_HISTORY_FILE = "data/trade_history.json"
BATCH_SIZE         = 100
MAX_PAGES          = 20   # 2,000 filings per window
MAX_TRADES_PER_KEY = 50
SLEEP_PER_PAGE     = 0.5
SLEEP_PER_FILING   = 0.15

# ── Only these dates failed — fill them in ───────────────────────────────────
GAP_DATES = [
    ("2026-02-23", "2026-02-24"),
    ("2025-09-06", "2025-09-07"),
    ("2025-04-25", "2025-04-26"),
]


def load_history():
    if os.path.exists(TRADE_HISTORY_FILE):
        with open(TRADE_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history):
    os.makedirs(os.path.dirname(TRADE_HISTORY_FILE), exist_ok=True)
    with open(TRADE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def fetch_xml(url):
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
    m = re.search(r"<value>(.*?)</value>", block, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else block.strip()


def parse_xml(xml_text):
    if not xml_text or len(xml_text) < 200:
        return None
    try:
        insider_name  = extract_tag("rptOwnerName", xml_text)
        insider_title = extract_tag("officerTitle", xml_text)
        ticker        = extract_tag("issuerTradingSymbol", xml_text)
        if not insider_name or not ticker:
            return None
        ticker = ticker.upper().strip()

        tx_blocks = re.findall(
            r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
            xml_text, re.DOTALL | re.IGNORECASE
        )

        trades = []
        for block in tx_blocks:
            code_block = extract_tag("transactionCode", block)
            code = get_value(code_block) if code_block else ""
            if code != "P":
                continue

            date_block   = extract_tag("transactionDate", block)
            date_val     = get_value(date_block) if date_block else ""
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
                "transaction_date": date_val,
                "shares_traded":   shares,
                "price_per_share": price,
                "total_value":     total,
                "shares_owned":    owned,
            })

        return trades if trades else None
    except Exception:
        return None


def record_trades(history, trades):
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
            "source":          "backfill_gaps",
        }

        existing_dates = {e.get("date") for e in history[key]}
        if entry["date"] not in existing_dates:
            history[key].append(entry)
            added += 1

        history[key] = sorted(
            history[key], key=lambda x: x.get("date", ""), reverse=True
        )[:MAX_TRADES_PER_KEY]

    return added


def fetch_window(start_date, end_date):
    filing_urls = []
    for page in range(MAX_PAGES):
        offset = page * BATCH_SIZE
        url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22%22&forms=4"
            f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
            f"&_source=file_date,period_ending,ciks,display_names,adsh"
            f"&from={offset}&size={BATCH_SIZE}"
        )
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
                filing_urls.append(
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{acc_clean}/{accession}.txt"
                )

        time.sleep(SLEEP_PER_PAGE)

    return filing_urls


def main():
    print("=" * 60)
    print("FORM4WIRE — GAP BACKFILL (3 FAILED DATE RANGES)")
    print("=" * 60)

    history = load_history()
    print(f"📂 Existing history keys: {len(history)}\n")

    total_added = 0

    for start, end in GAP_DATES:
        print(f"Processing {start} → {end} ...", end=" ", flush=True)
        filing_urls = fetch_window(start, end)

        if not filing_urls:
            print("⚠️  Still no filings — SEC may still have issues for this date")
            continue

        window_added  = 0
        window_parsed = 0

        for url in filing_urls:
            xml = fetch_xml(url)
            if xml:
                trades = parse_xml(xml)
                if trades:
                    window_parsed += 1
                    window_added += record_trades(history, trades)
            time.sleep(SLEEP_PER_FILING)

        total_added += window_added
        save_history(history)
        print(f"{len(filing_urls)} filings, {window_parsed} w/buys, {window_added} records added")

    print(f"\n✅ Total new records added: {total_added}")
    print(f"✅ Unique insider keys: {len(history)}")
    print(f"\n📂 Saved to: {TRADE_HISTORY_FILE}")


if __name__ == "__main__":
    main()
