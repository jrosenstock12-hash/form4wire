"""
validate_tweet.py — Validates a posted tweet against live trade history on Railway.

Usage:
    railway run python3 validate_tweet.py TICKER "INSIDER NAME"
    railway run python3 validate_tweet.py TICKER "INSIDER NAME" --value 526000 --shares 18500 --price 28.41 --before 468277 --title "Chief Executive Officer"

Examples:
    railway run python3 validate_tweet.py SENS "Goodnow Timothy T"
    railway run python3 validate_tweet.py MBX "Hawryluk P. Kent" --value 526000 --shares 18500 --price 28.41 --before 468277 --title "Chief Executive Officer"
"""

import json
import requests
import sys
import os
from datetime import datetime, timezone

TRADE_HISTORY_FILE = "data/trade_history.json"


def load_history():
    if os.path.exists(TRADE_HISTORY_FILE):
        with open(TRADE_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def months_between(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days // 30
    except Exception:
        return 999


def check_history(ticker, insider_name):
    history = load_history()
    key = f"{ticker}:{insider_name}"
    trades = history.get(key, [])

    print(f"\n{'='*60}")
    print(f"HISTORY CHECK: {key}")
    print(f"{'='*60}")

    if not trades:
        print(f"  ⚠️  No history found for this insider/ticker combo")
        print(f"  → 'First insider buy in 12+ months' WOULD fire (no prior data)")
        print(f"  → This may be correct if they truly have no prior buys, or")
        print(f"     may indicate the backfill missed their prior purchases")
        return None

    if not isinstance(trades, list):
        print(f"  ❌ Unexpected data format in history")
        return None

    trades_sorted = sorted(trades, key=lambda x: x.get("date", ""), reverse=True)
    last = trades_sorted[0]
    months = months_between(last.get("date", ""))
    unusual = months >= 12

    print(f"  Total trades in history: {len(trades)}")
    print(f"  Most recent prior buy:   {last.get('date')} (${last.get('total_value',0):,.0f})")
    print(f"  Months since last buy:   {months}")
    print(f"  'unusual' flag:          {'✅ TRUE — first buy in 12+ months (correct to show line)' if unusual else '❌ FALSE — bought within 12 months (line should NOT appear)'}")
    print(f"\n  All trades in history:")
    for t in trades_sorted:
        print(f"    {t.get('date')} | ${t.get('total_value',0):,.0f} | source={t.get('source','live')}")

    return months




def check_cluster(ticker):
    sep = "=" * 60
    print("")
    print(sep)
    print("CLUSTER CHECK: " + ticker)
    print(sep)
    if not os.path.exists("data/cluster_tracker.json"):
        print("  No cluster tracker file found")
        return 0
    with open("data/cluster_tracker.json") as f:
        clusters = json.load(f)
    data = clusters.get(ticker, {})
    trades = data.get("trades", [])
    if not trades:
        print("  No cluster data for " + ticker + " -> +0")
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - 7 * 86400
    recent = []
    for t in trades:
        try:
            dt = datetime.fromisoformat(t.get("saved_at","").replace("Z","+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() >= cutoff:
                recent.append(t)
        except Exception:
            pass
    unique = len(set(t.get("insider","") for t in recent))
    pts = 3 if unique >= 3 else (2 if unique >= 2 else 0)
    if pts:
        print("  CLUSTER: " + str(unique) + " insiders in last 7 days -> +" + str(pts))
        for t in recent:
            print("    " + t.get("insider","") + " | " + t.get("date",""))
    else:
        print("  No cluster (" + str(unique) + " insider) -> +0")
    return pts


def check_stock(ticker, trade_price):
    sep = "=" * 60
    print("")
    print(sep)
    print("STOCK CHECK: " + ticker)
    print(sep)
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker + "?interval=1d&range=1y"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        high = meta.get("fiftyTwoWeekHigh", 0)
        low  = meta.get("fiftyTwoWeekLow", 0)
        curr = meta.get("regularMarketPrice", 0)
        print("  Current: $" + str(round(curr,2)) + " | 52W High: $" + str(round(high,2)) + " | 52W Low: $" + str(round(low,2)))
        if high > 0 and trade_price > 0:
            pct = (high - trade_price) / high * 100
            print("  At trade price $" + str(trade_price) + ": -" + str(round(pct,1)) + "% from 52W high")
            if pct > 40:
                print("  Stock down >40% -> +1")
                return 1
            else:
                print("  Not down >40% -> +0")
        return 0
    except Exception as e:
        print("  Could not fetch stock data: " + str(e))
        return 0
