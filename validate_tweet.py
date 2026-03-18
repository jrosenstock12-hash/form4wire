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
        if high > 0 and curr > 0:
            pct = (high - curr) / high * 100
            print("  At current price $" + str(round(curr,2)) + ": -" + str(round(pct,1)) + "% from 52W high")
            if pct > 40:
                print("  Stock down >40% -> +1")
                return 1
            else:
                print("  Not down >40% -> +0")
        return 0
    except Exception as e:
        print("  Could not fetch stock data: " + str(e))
        return 0


def check_earnings(ticker):
    sep = "=" * 60
    print("")
    print(sep)
    print("EARNINGS CHECK: " + ticker)
    print(sep)
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        events = data["quoteSummary"]["result"][0]["calendarEvents"]
        dates = events.get("earnings", {}).get("earningsDate", [])
        if not dates:
            print("  No earnings date available -> +0")
            return 0, ""
        from datetime import datetime, timezone
        ts = dates[0].get("raw", 0)
        earn_dt = datetime.utcfromtimestamp(ts).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        days = (earn_dt - now).days
        earn_str = earn_dt.strftime("%b %d, %Y")
        if days < 0:
            print(f"  Earnings date {earn_str} already passed -> +0")
            return 0, earn_str
        print(f"  Next earnings: {earn_str} ({days} days away)")
        if days <= 21:
            print(f"  Within 21 days -> +1")
            return 1, earn_str
        else:
            print(f"  More than 21 days away -> +0")
            return 0, earn_str
    except Exception as e:
        print("  Could not fetch earnings data: " + str(e))
        return 0, ""


def check_score(ticker, insider_name, total_value, shares, price, before_shares, title, days, cluster_pts=0, high_pts=0, earn_pts=0):
    sep = "=" * 60
    print("")
    print(sep)
    print("SCORE VALIDATION: " + ticker)
    print(sep)
    t = title.lower()
    if any(x in t for x in ["executive vice", "senior vice", "vice president", "evp", "svp", " vp", "director", "board", "treasurer"]):
        role_pts, role_label = 1, "VP/Director/Board (+1)"
    elif any(x in t for x in ["chief executive", "ceo", "chairman", "founder"]) or ("president" in t and "vice" not in t):
        role_pts, role_label = 3, "CEO/Chairman/Founder/President (+3)"
    elif any(x in t for x in ["chief financial", "cfo", "chief operating", "coo", "chief tech", "cto", "general counsel"]):
        role_pts, role_label = 2, "C-Suite officer (+2)"
    else:
        role_pts, role_label = 1, "Other insider (+1)"
    if total_value >= 1000000:
        val_pts, val_label = 3, "$" + str(round(total_value/1e6,1)) + "M -> >$1M (+3)"
    elif total_value >= 500000:
        val_pts, val_label = 2, "$" + str(round(total_value/1e3)) + "K -> $500K-$1M (+2)"
    elif total_value >= 100000:
        val_pts, val_label = 1, "$" + str(round(total_value/1e3)) + "K -> $100K-$500K (+1)"
    else:
        val_pts, val_label = 0, "$" + str(round(total_value/1e3)) + "K -> under $100K (+0)"
    pos_pts, pos_label = 0, "No before-shares data (+0)"
    if before_shares > 0 and shares > 0:
        pct = (shares / before_shares) * 100
        if pct > 50:
            pos_pts, pos_label = 3, "+" + str(round(pct)) + "% -> >50% (+3)"
        elif pct > 25:
            pos_pts, pos_label = 2, "+" + str(round(pct)) + "% -> 25-50% (+2)"
        elif pct > 10:
            pos_pts, pos_label = 1, "+" + str(round(pct)) + "% -> 10-25% (+1)"
        else:
            pos_pts, pos_label = 0, "+" + str(round(pct)) + "% -> <10% (+0)"
    if days is None:
        unusual_pts, unusual_label = 1, "No history -> unusual fires (+1)"
    elif days >= 365:
        unusual_pts, unusual_label = 1, str(days) + " days since last buy -> >=365 days (+1)"
    else:
        unusual_pts, unusual_label = 0, str(days) + " days since last buy -> <365 days (+0)"
    raw = role_pts + val_pts + pos_pts + unusual_pts + cluster_pts + high_pts + earn_pts
    final = max(1, min(10, raw))
    print("  Title:    " + title)
    print("  " + "-"*41)
    print("  Role:     " + role_label)
    print("  Value:    " + val_label)
    print("  Position: " + pos_label)
    print("  History:  " + unusual_label)
    print("  Cluster:  +" + str(cluster_pts))
    print("  52W High: +" + str(high_pts))
    print("  Earnings: +" + str(earn_pts))
    print("  " + "-"*41)
    print("  Raw total: " + str(raw) + " -> Final score: " + str(final) + "/10")


def main():
    import sys
    if len(sys.argv) < 3:
        print("Usage: railway run python3 validate_tweet.py TICKER \"INSIDER NAME\"")
        sys.exit(1)
    ticker  = sys.argv[1].upper()
    insider = sys.argv[2]
    args    = sys.argv[3:]
    def get_arg(name, default=0):
        try: return float(args[args.index(name) + 1])
        except: return default
    def get_str_arg(name, default=""):
        try: return args[args.index(name) + 1]
        except: return default
    total_value = get_arg("--value")
    shares      = get_arg("--shares")
    price       = get_arg("--price")
    before      = get_arg("--before")
    title       = get_str_arg("--title")
    days        = check_history(ticker, insider)
    cluster_pts = check_cluster(ticker)
    high_pts    = check_stock(ticker, price) if price else 0
    earn_pts, _ = check_earnings(ticker)
    if title:
        check_score(ticker, insider, total_value, shares, price, before, title, days, cluster_pts, high_pts, earn_pts)
    print("")
    print("=" * 60)
    print("")


if __name__ == "__main__":
    main()
