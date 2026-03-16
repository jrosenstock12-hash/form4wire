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
    if not os.path.exists("data/cluster_tracker.json"):
        print("
" + "="*60)
        print("CLUSTER CHECK: " + ticker)
        print("="*60)
        print("  No cluster tracker file found")
        return 0
    with open("data/cluster_tracker.json") as f:
        clusters = json.load(f)
    data = clusters.get(ticker, {})
    trades = data.get("trades", [])
    print("
" + "="*60)
    print("CLUSTER CHECK: " + ticker)
    print("="*60)
    if not trades:
        print("  No cluster data → +0")
        return 0
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    cutoff = now.timestamp() - 7 * 86400
    recent = []
    for t in trades:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(t.get("saved_at","").replace("Z","+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() >= cutoff: recent.append(t)
        except: pass
    unique = len(set(t.get("insider","") for t in recent))
    pts = 3 if unique >= 3 else (2 if unique >= 2 else 0)
    if pts:
        print(f"  CLUSTER: {unique} insiders in last 7 days → +{pts}")
        for t in recent: print(f"    {t.get('insider','')} | {t.get('date','')}")
    else:
        print(f"  No cluster ({unique} insider) → +0")
    return pts


def check_stock(ticker, trade_price):
    print("
" + "="*60)
    print("STOCK CHECK: " + ticker)
    print("="*60)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        high = meta.get("fiftyTwoWeekHigh", 0)
        low  = meta.get("fiftyTwoWeekLow", 0)
        curr = meta.get("regularMarketPrice", 0)
        print(f"  Current: ${curr:.2f} | 52W High: ${high:.2f} | 52W Low: ${low:.2f}")
        if high > 0:
            pct = (high - trade_price) / high * 100
            print(f"  At trade price ${trade_price:.2f}: -{pct:.1f}% from 52W high")
            if pct > 40:
                print(f"  Stock down >40% → +1")
                return 1
            else:
                print(f"  Not down >40% → +0")
        return 0
    except Exception as e:
        print(f"  Could not fetch: {e}")
        return 0

def check_score(ticker, insider_name, total_value, shares, price, before_shares, title, days, cluster_pts, high_pts):
    print(f"\n{'='*60}")
    print(f"SCORE VALIDATION: {ticker}")
    print(f"{'='*60}")

    t = title.lower()

    # Role — check VP/Director FIRST
    if any(x in t for x in ["executive vice", "senior vice", "vice president", "evp", "svp", " vp", "director", "board", "treasurer"]):
        role_pts, role_label = 1, "VP/Director/Board (+1)"
    elif any(x in t for x in ["chief executive", "ceo", "chairman", "founder"]) or ("president" in t and "vice" not in t):
        role_pts, role_label = 3, "CEO/Chairman/Founder/President (+3)"
    elif any(x in t for x in ["chief financial", "cfo", "chief operating", "coo", "chief tech", "cto", "general counsel"]):
        role_pts, role_label = 2, "C-Suite officer (+2)"
    else:
        role_pts, role_label = 1, "Other insider (+1)"

    # Value
    if total_value >= 1_000_000:
        val_pts, val_label = 3, f"${total_value/1e6:.1f}M → >$1M (+3)"
    elif total_value >= 500_000:
        val_pts, val_label = 2, f"${total_value/1e3:.0f}K → $500K-$1M (+2)"
    elif total_value >= 100_000:
        val_pts, val_label = 1, f"${total_value/1e3:.0f}K → $100K-$500K (+1)"
    else:
        val_pts, val_label = 0, f"${total_value/1e3:.0f}K → under $100K (+0)"

    # Position
    pos_pts, pos_label = 0, "No before-shares data (+0)"
    if before_shares > 0 and shares > 0:
        pct = (shares / before_shares) * 100
        if pct > 50:
            pos_pts, pos_label = 3, f"+{pct:.0f}% position increase → >50% (+3)"
        elif pct > 25:
            pos_pts, pos_label = 2, f"+{pct:.0f}% position increase → 25-50% (+2)"
        elif pct > 10:
            pos_pts, pos_label = 1, f"+{pct:.0f}% position increase → 10-25% (+1)"
        else:
            pos_pts, pos_label = 0, f"+{pct:.0f}% position increase → <10% (+0)"

    # Unusual
    if months is None:
        unusual_pts, unusual_label = 1, "No history found → unusual fires (+1)"
    elif months >= 12:
        unusual_pts, unusual_label = 1, f"{months} months since last buy → ≥12 months (+1)"
    else:
        unusual_pts, unusual_label = 0, f"{months} months since last buy → <12 months (+0)"

    raw = role_pts + val_pts + pos_pts + unusual_pts
    final = max(1, min(10, raw))

    print(f"  Title:    {title}")
    print(f"  ─────────────────────────────────────────")
    print(f"  Role:     {role_label}")
    print(f"  Value:    {val_label}")
    print(f"  Position: {pos_label}")
    print(f"  History:  {unusual_label}")
    print(f"  Cluster:  +{cluster_pts}")
    print(f"  52W High: +{high_pts}")
    print(f"  ─────────────────────────────────────────")
    print(f"  Raw total: {raw} → Final score: {final}/10")


def main():
    if len(sys.argv) < 3:
        print("Usage: railway run python3 validate_tweet.py TICKER \"INSIDER NAME\"")
        print("Optional: --value 526000 --shares 18500 --price 28.41 --before 468277 --title \"CEO\"")
        sys.exit(1)

    ticker = sys.argv[1].upper()
    insider = sys.argv[2]

    args = sys.argv[3:]

    def get_arg(name, default=0):
        try:
            return float(args[args.index(name) + 1])
        except (ValueError, IndexError):
            return default

    def get_str_arg(name, default=""):
        try:
            return args[args.index(name) + 1]
        except (ValueError, IndexError):
            return default

    total_value  = get_arg("--value")
    shares       = get_arg("--shares")
    price        = get_arg("--price")
    before       = get_arg("--before")
    title        = get_str_arg("--title")

    days        = check_history(ticker, insider)
    cluster_pts = check_cluster(ticker)
    high_pts    = check_stock(ticker, price) if price else 0

    if title:
        check_score(ticker, insider, total_value, shares, price, before, title, days, cluster_pts, high_pts)

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
