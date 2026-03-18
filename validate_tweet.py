"""
validate_tweet.py — Validates a posted tweet against live trade history on Railway.

Usage:
    railway run python3 validate_tweet.py TICKER "INSIDER NAME"
    railway run python3 validate_tweet.py TICKER "INSIDER NAME" --value 526000 --shares 18500 --price 28.41 --before 468277 --title "Chief Executive Officer"

Notes:
    --title is now optional. The validator automatically fetches the real title and remarks
    from the SEC EDGAR filing and upgrades the role score if the filing reveals a higher role
    (e.g. Director who is also Principal Executive Officer).
    --before is also optional — shares_before will be pulled from the SEC filing if not provided.

Examples:
    railway run python3 validate_tweet.py GPGI "Knott Thomas R" --value 752000 --shares 44000 --price 17.08
    railway run python3 validate_tweet.py MBX "Hawryluk P. Kent" --value 526000 --shares 18500 --price 28.41
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


SEC_HEADERS = {
    "User-Agent": "Form4Wire support@form4wire.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,*/*",
}

def fetch_sec_filing(ticker, insider_name):
    """
    Look up the most recent Form 4 for this insider/ticker on SEC EDGAR.
    Returns dict with real_title, remarks, shares_owned_before, shares_owned_after.
    """
    sep = "=" * 60
    print("")
    print(sep)
    print("SEC FILING LOOKUP: " + ticker + " / " + insider_name)
    print(sep)
    try:
        # Search SEC EDGAR for recent Form 4 filings
        from datetime import datetime, timedelta
        today = datetime.utcnow().date()
        start = today - timedelta(days=14)
        search_url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{insider_name.replace(' ', '+')}%22"
            f"&forms=4&dateRange=custom&startdt={start}&enddt={today}"
            f"&_source=file_date,display_names,adsh,ciks&from=0&size=10"
        )
        r = requests.get(search_url, headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])

        if not hits:
            print("  No recent filings found on SEC EDGAR")
            return {}

        # Find the hit that matches our ticker
        target_hit = None
        for hit in hits:
            names = hit.get("_source", {}).get("display_names", [])
            names_str = " ".join(names).upper()
            if ticker.upper() in names_str or insider_name.upper() in names_str:
                target_hit = hit
                break

        if not target_hit:
            target_hit = hits[0]  # Fall back to most recent

        source    = target_hit.get("_source", {})
        filing_id = target_hit.get("_id", "")
        ciks      = source.get("ciks", [""])
        cik       = ciks[0] if ciks else ""
        accession = filing_id.split(":")[0] if ":" in filing_id else filing_id
        acc_clean = accession.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"

        print(f"  Found filing: {accession}")

        # Fetch the index page to find XML
        import re
        resp = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        xml_matches = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        raw_xml_url = None
        for match in xml_matches:
            if "xsl" not in match.lower():
                raw_xml_url = "https://www.sec.gov" + match
                break

        if not raw_xml_url:
            print("  Could not find XML in filing index")
            return {}

        import time as _time
        _time.sleep(0.3)
        xml_resp = requests.get(raw_xml_url, headers=SEC_HEADERS, timeout=15)
        xml_resp.raise_for_status()
        xml = xml_resp.text

        # Parse the XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)

        # Extract officer title
        real_title = ""
        for rel in root.findall(".//reportingOwnerRelationship"):
            title_el = rel.find("officerTitle")
            if title_el is not None and title_el.text:
                real_title = title_el.text.strip()
                break

        # Extract remarks
        remarks = ""
        remarks_el = root.find(".//remarks")
        if remarks_el is not None and remarks_el.text:
            remarks = remarks_el.text.strip()

        # Extract relationship flags
        is_officer = False
        is_director = False
        is_ten_pct = False
        for rel in root.findall(".//reportingOwnerRelationship"):
            if rel.find("isOfficer") is not None and rel.find("isOfficer").text == "1":
                is_officer = True
            if rel.find("isDirector") is not None and rel.find("isDirector").text == "1":
                is_director = True
            if rel.find("isTenPercentOwner") is not None and rel.find("isTenPercentOwner").text == "1":
                is_ten_pct = True

        # Extract shares owned before/after from first P transaction
        shares_before = 0
        shares_after = 0
        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el = txn.find(".//transactionCoding/transactionCode")
            if code_el is None or code_el.text.strip() != "P":
                continue
            owned_el = txn.find(".//sharesOwnedFollowingTransaction/value")
            shares_el = txn.find(".//transactionShares/value")
            if owned_el is not None and owned_el.text:
                shares_after = int(float(owned_el.text.strip()))
            if shares_el is not None and shares_el.text:
                traded = int(float(shares_el.text.strip()))
                shares_before = max(0, shares_after - traded)
            break

        print(f"  Officer title: '{real_title}'")
        print(f"  Remarks:       '{remarks[:100]}'" if remarks else "  Remarks:       (none)")
        print(f"  Is officer:    {is_officer} | Is director: {is_director} | 10% owner: {is_ten_pct}")
        print(f"  Shares before: {shares_before:,} | Shares after: {shares_after:,}")

        return {
            "real_title":    real_title,
            "remarks":       remarks,
            "is_officer":    is_officer,
            "is_director":   is_director,
            "is_ten_pct":    is_ten_pct,
            "shares_before": shares_before,
            "shares_after":  shares_after,
        }

    except Exception as e:
        print(f"  SEC lookup failed: {e}")
        return {}


def resolve_title(title_arg, sec_data):
    """
    Given a manually passed title and SEC filing data, return the best title to use for scoring.
    Upgrades title if SEC remarks reveal a higher role.
    """
    real_title = sec_data.get("real_title", "")
    remarks    = sec_data.get("remarks", "").lower()

    # Build combined text to check
    combined = (real_title + " " + remarks).lower()

    # Check for CEO-level keywords in combined text
    ceo_keywords = ["chief executive", "ceo", "principal executive", "chairman", "founder", "co-founder"]
    cfo_keywords = ["chief financial", "cfo", "chief operating", "coo", "chief technology", "cto",
                    "chief investment", "cio", "general counsel", "chief legal"]

    if any(k in combined for k in ceo_keywords):
        best = real_title if real_title else "Chief Executive Officer"
        if best.lower() != title_arg.lower():
            print(f"  ⬆️  Title upgraded: '{title_arg}' → '{best}' (from SEC filing)")
        return best
    elif any(k in combined for k in cfo_keywords):
        best = real_title if real_title else title_arg
        if best.lower() != title_arg.lower():
            print(f"  ⬆️  Title upgraded: '{title_arg}' → '{best}' (from SEC filing)")
        return best

    # If real_title found and different, prefer it
    if real_title and real_title.lower() != title_arg.lower():
        print(f"  ℹ️  SEC title differs: '{title_arg}' vs '{real_title}' — using SEC title")
        return real_title

    return title_arg


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


def check_streak(ticker, insider_name):
    sep = "=" * 60
    print("")
    print(sep)
    print("STREAK CHECK: " + ticker + " / " + insider_name)
    print(sep)
    history = load_history()
    # normalize name to match storage key
    norm = " ".join(w.capitalize() for w in insider_name.strip().split())
    key = f"{ticker}:{norm}"
    trades = history.get(key, [])
    if not trades or not isinstance(trades, list):
        print("  No history -> streak +0")
        return 0
    buys = sorted(
        [t for t in trades if t.get("is_buy") and t.get("date")],
        key=lambda x: x.get("date", ""),
        reverse=True
    )
    if len(buys) < 2:
        print("  Fewer than 2 buys in history -> streak +0")
        return 0
    consecutive = 0
    prev_date_str = buys[0].get("date", "")
    for t in buys[1:]:
        try:
            prev_dt = datetime.strptime(prev_date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            curr_dt = datetime.strptime(t["date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            gap_days = (prev_dt - curr_dt).days
            if gap_days > 180:
                break
            consecutive += 1
            prev_date_str = t["date"]
        except Exception:
            break
    if consecutive >= 2:
        print(f"  {consecutive} consecutive buys within 180 days -> +1")
        for b in buys[:consecutive+1]:
            print(f"    {b.get('date')} | ${b.get('total_value',0):,.0f}")
        return 1
    elif consecutive == 1:
        print(f"  1 prior buy within 180 days (this would be 2nd consecutive) -> +1")
        return 1
    else:
        print(f"  No consecutive buys within 180 days -> +0")
        return 0


def check_followups(ticker, insider_name, entry_price):
    sep = "=" * 60
    print("")
    print(sep)
    print("FOLLOWUP CHECK: " + ticker + " / " + insider_name)
    print(sep)

    FOLLOWUP_QUEUE_FILE = "data/followup_queue.json"
    if not os.path.exists(FOLLOWUP_QUEUE_FILE):
        print("  No followup queue file found")
        return

    with open(FOLLOWUP_QUEUE_FILE) as f:
        queue = json.load(f)

    norm = " ".join(w.capitalize() for w in insider_name.strip().split())
    matches = [
        item for item in queue
        if item["trade"].get("ticker", "").upper() == ticker.upper()
        and norm.lower() in (item["trade"].get("insider_name", "") or "").lower()
    ]

    if not matches:
        print("  No followup queue entries found for this trade")
        return

    # Fetch current price
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        current_price = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice", 0)
    except Exception:
        current_price = 0

    if entry_price and current_price:
        change_pct = ((current_price - entry_price) / entry_price) * 100
        print(f"  Entry: ${entry_price:.2f} → Current: ${current_price:.2f} ({change_pct:+.1f}%)")

    for item in sorted(matches, key=lambda x: x.get("days", 0)):
        days     = item.get("days", 0)
        posted   = item.get("posted", False)
        prior    = item.get("prior_followup_posted", False)
        orig_id  = item.get("original_tweet_id", "none")
        due_date = item.get("due_date", "")[:10]

        status = "✅ POSTED" if posted else ("⏭ SKIPPED (prior posted)" if prior else "⏳ PENDING")

        would_fire = ""
        if entry_price and current_price and not posted and not prior:
            change_pct = ((current_price - entry_price) / entry_price) * 100
            if change_pct >= 10.0:
                would_fire = f" → would POST (up {change_pct:+.1f}%)"
            elif change_pct <= -20.0 and days == 90:
                would_fire = f" → would POST loss tweet ({change_pct:+.1f}%)"
            else:
                would_fire = f" → would SKIP ({change_pct:+.1f}%, threshold not met)"

        print(f"  {days}-day: {status} | due {due_date} | reply_to={orig_id}{would_fire}")


def check_score(ticker, insider_name, total_value, shares, price, before_shares, title, days, cluster_pts=0, high_pts=0, earn_pts=0, streak_pts=0, remarks=""):
    sep = "=" * 60
    print("")
    print(sep)
    print("SCORE VALIDATION: " + ticker)
    print(sep)
    combined = (title + " " + remarks).lower()
    t = title.lower()
    # Role scoring — mirrors _role_score in ai_parser.py including remarks
    ceo_terms = ["chief executive", "chairman", "founder", "co-founder", "principal executive"]
    csuite_terms = ["chief financial", "chief operating", "general counsel", "chief legal",
                    "chief technology", "chief revenue", "chief marketing", "chief information",
                    "chief accounting", "chief medical", "chief scientific", "chief compliance",
                    "chief human", "chief people", "chief strategy", "chief data", "chief investment"]
    vp_terms = ["executive vice", "senior vice", "vice president", "evp", "svp", " vp",
                "director", "board", "treasurer"]
    if any(x in combined for x in ceo_terms):
        src = " (from remarks)" if any(x in remarks.lower() for x in ceo_terms) and not any(x in t for x in ceo_terms) else ""
        role_pts, role_label = 3, f"CEO/Chairman/Founder/President (+3){src}"
    elif "president" in combined and "vice" not in combined:
        role_pts, role_label = 3, "President (+3)"
    elif any(x in combined for x in csuite_terms):
        src = " (from remarks)" if any(x in remarks.lower() for x in csuite_terms) and not any(x in t for x in csuite_terms) else ""
        role_pts, role_label = 2, f"C-Suite officer (+2){src}"
    elif any(x in t for x in ["cfo", "coo", "cto", "cro", "cmo", "cio", "cao", "cco", "chro", "cso"]):
        role_pts, role_label = 2, "C-Suite officer (+2)"
    elif any(x in t for x in vp_terms):
        role_pts, role_label = 1, "VP/Director/Board (+1)"
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
    raw = role_pts + val_pts + pos_pts + unusual_pts + cluster_pts + high_pts + earn_pts + streak_pts
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
    print("  Streak:   +" + str(streak_pts))
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

    # Fetch real title and remarks from SEC filing
    sec_data = fetch_sec_filing(ticker, insider)

    # Use SEC shares_before if not manually provided
    if not before and sec_data.get("shares_before"):
        before = sec_data["shares_before"]
        print(f"  → Using shares_before from SEC filing: {int(before):,}")

    # Resolve best title from SEC data
    if title:
        title = resolve_title(title, sec_data)
    elif sec_data.get("real_title"):
        title = sec_data["real_title"]
        print(f"  → Using title from SEC filing: '{title}'")

    days        = check_history(ticker, insider)
    cluster_pts = check_cluster(ticker)
    high_pts    = check_stock(ticker, price) if price else 0
    earn_pts, _ = check_earnings(ticker)
    streak_pts  = check_streak(ticker, insider)
    check_followups(ticker, insider, price)
    if title:
        remarks = sec_data.get("remarks", "")
        check_score(ticker, insider, total_value, shares, price, before, title, days, cluster_pts, high_pts, earn_pts, streak_pts, remarks)
    print("")
    print("=" * 60)
    print("")


if __name__ == "__main__":
    main()
