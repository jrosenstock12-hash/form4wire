"""
post_validator.py — Automatically validates every posted tweet and sends an email report.
Called by bot.py immediately after every successful post to X.
"""

import os
import re
import smtplib
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

SEC_HEADERS = {
    "User-Agent": "Form4Wire support@form4wire.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,*/*",
}


# ── ROLE SCORING (mirrors ai_parser._role_score) ─────────────────────────────

def _role_score(title: str, remarks: str = "") -> tuple[int, str]:
    combined = (title + " " + remarks).lower().strip()
    t = title.lower().strip()
    ceo_terms = ["chief executive", "chairman", "founder", "co-founder", "principal executive"]
    csuite_terms = [
        "chief financial", "chief operating", "general counsel", "chief legal",
        "chief technology", "chief revenue", "chief marketing", "chief information",
        "chief accounting", "chief medical", "chief scientific", "chief compliance",
        "chief human", "chief people", "chief strategy", "chief data", "chief investment",
    ]
    if any(x in combined for x in ceo_terms):
        src = " (from remarks)" if any(x in remarks.lower() for x in ceo_terms) and not any(x in t for x in ceo_terms) else ""
        return 3, f"CEO/Chairman/Founder (+3){src}"
    if "president" in combined and "vice" not in combined:
        return 3, "President (+3)"
    if any(x in combined for x in csuite_terms):
        src = " (from remarks)" if any(x in remarks.lower() for x in csuite_terms) and not any(x in t for x in csuite_terms) else ""
        return 2, f"C-Suite (+2){src}"
    if any(t == x or t.startswith(x+" ") or t.endswith(" "+x)
           for x in ["cfo","coo","cto","cro","cmo","cio","cao","cco","chro","cso"]):
        return 2, "C-Suite (+2)"
    if any(x in t for x in ["executive vice","senior vice","vice president",
                              "evp","svp","treasurer","secretary",
                              "controller","director","board"," vp"]):
        return 1, "VP/Director/Board (+1)"
    return 1, "Other insider (+1)"


# ── SEC FILING LOOKUP ─────────────────────────────────────────────────────────

def _fetch_sec_filing(ticker: str, insider_name: str) -> dict:
    """Fetch real title, remarks, and shares_before from SEC EDGAR."""
    try:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=14)
        search_url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{insider_name.replace('', '+')}%22"
            f"&forms=4&dateRange=custom&startdt={start}&enddt={today}"
            f"&_source=file_date,display_names,adsh,ciks&from=0&size=10"
        )
        r = requests.get(search_url, headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        if not hits:
            return {}

        target = None
        for hit in hits:
            names_str = " ".join(hit.get("_source", {}).get("display_names", [])).upper()
            if ticker.upper() in names_str or insider_name.upper() in names_str:
                target = hit
                break
        if not target:
            target = hits[0]

        source    = target.get("_source", {})
        filing_id = target.get("_id", "")
        cik       = (source.get("ciks") or [""])[0]
        accession = filing_id.split(":")[0] if ":" in filing_id else filing_id
        acc_clean = accession.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"

        resp = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        xml_matches = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        raw_xml_url = next((f"https://www.sec.gov{m}" for m in xml_matches if "xsl" not in m.lower()), None)
        if not raw_xml_url:
            return {}

        import time as _time
        _time.sleep(0.3)
        xml_resp = requests.get(raw_xml_url, headers=SEC_HEADERS, timeout=15)
        xml_resp.raise_for_status()
        xml = xml_resp.text
        root = ET.fromstring(xml)

        real_title = ""
        for rel in root.findall(".//reportingOwnerRelationship"):
            t = rel.find("officerTitle")
            if t is not None and t.text:
                real_title = t.text.strip()
                break

        remarks = ""
        r_el = root.find(".//remarks")
        if r_el is not None and r_el.text:
            remarks = r_el.text.strip()

        if real_title.lower() in ("see remarks", "see footnote", "") and remarks:
            real_title = remarks[:100]

        shares_before = 0
        shares_after  = 0
        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el = txn.find(".//transactionCoding/transactionCode")
            if code_el is None or code_el.text.strip() != "P":
                continue
            owned_el  = txn.find(".//sharesOwnedFollowingTransaction/value")
            shares_el = txn.find(".//transactionShares/value")
            if owned_el is not None and owned_el.text:
                shares_after = int(float(owned_el.text.strip()))
            if shares_el is not None and shares_el.text:
                traded = int(float(shares_el.text.strip()))
                shares_before = max(0, shares_after - traded)
            break

        return {
            "real_title":    real_title,
            "remarks":       remarks,
            "shares_before": shares_before,
            "shares_after":  shares_after,
        }
    except Exception as e:
        log.warning(f"[Validator] SEC lookup failed: {e}")
        return {}


# ── SCORE CALCULATION (mirrors calculate_base_score) ─────────────────────────

def _calculate_score(trade: dict, sec_data: dict, stock: dict, history: dict, next_earnings: str = "") -> tuple[int, dict]:
    title    = sec_data.get("real_title") or trade.get("insider_title", "")
    remarks  = sec_data.get("remarks", "")
    code     = trade.get("transaction_code", "")
    total    = trade.get("total_value", 0)
    traded   = trade.get("shares_traded", 0)
    before   = sec_data.get("shares_before") or trade.get("shares_owned_before", 0)
    unusual  = history.get("unusual", True)
    cluster  = history.get("cluster_count", 0)
    price    = stock.get("price", 0)
    high_52w = stock.get("52w_high", 0)
    consec   = history.get("consecutive_buys", 0)

    pts = {}

    role_pts, role_label = _role_score(title, remarks)
    pts["Role"] = (role_pts, role_label)

    if total >= 1_000_000:   pts["Value"] = (3, f"${ total/1e6:.1f}M → +3")
    elif total >= 500_000:   pts["Value"] = (2, f"${ total/1e3:.0f}K → +2")
    elif total >= 100_000:   pts["Value"] = (1, f"${ total/1e3:.0f}K → +1")
    else:                    pts["Value"] = (0, f"${ total/1e3:.0f}K → +0")

    if before >= 100 and traded > 0 and code == "P":
        pct = (traded / before) * 100
        if pct > 50:   pts["Position"] = (3, f"+{pct:.0f}% → +3")
        elif pct > 25: pts["Position"] = (2, f"+{pct:.0f}% → +2")
        elif pct > 10: pts["Position"] = (1, f"+{pct:.0f}% → +1")
        else:          pts["Position"] = (0, f"+{pct:.0f}% → +0")
    else:
        pts["Position"] = (0, "No before-shares data → +0")

    if cluster >= 3:   pts["Cluster"] = (3, f"{cluster} insiders → +3")
    elif cluster >= 2: pts["Cluster"] = (2, f"{cluster} insiders → +2")
    else:              pts["Cluster"] = (0, "No cluster → +0")

    if price and high_52w and code == "P":
        pct_high = (high_52w - price) / high_52w
        if pct_high > 0.40: pts["52W High"] = (1, f"−{pct_high*100:.0f}% from high → +1")
        else:               pts["52W High"] = (0, f"−{pct_high*100:.0f}% from high → +0")
    else:
        pts["52W High"] = (0, "+0")

    if unusual: pts["Unusual"] = (1, "No buys in 12+ months → +1")
    else:       pts["Unusual"] = (0, "Recent buy → +0")

    if consec >= 2: pts["Streak"] = (1, f"{consec} consecutive buys → +1")
    else:           pts["Streak"] = (0, "No streak → +0")

    # Earnings proximity
    earn_pts = 0
    if next_earnings:
        try:
            earn_dt = datetime.strptime(next_earnings, "%b %d, %Y").replace(tzinfo=timezone.utc)
            days = (earn_dt - datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)).days
            if 0 <= days <= 21 and code == "P":
                earn_pts = 1
                pts["Earnings"] = (1, f"{days} days to earnings → +1")
            else:
                pts["Earnings"] = (0, f"{days} days to earnings → +0")
        except Exception:
            pts["Earnings"] = (0, "+0")
    else:
        pts["Earnings"] = (0, "+0")

    raw   = sum(v for v, _ in pts.values())
    final = max(1, min(10, raw))
    return final, pts


# ── EMAIL SENDER ──────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str):
    # Read credentials at call time — not import time — so env var changes take effect
    gmail_user         = os.environ.get("GMAIL_USER", "")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    alert_email        = os.environ.get("ALERT_EMAIL", "")

    if not gmail_user or not gmail_app_password or not alert_email:
        log.warning("[Validator] Email credentials not set — skipping email")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = gmail_user
        msg["To"]      = alert_email
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, alert_email, msg.as_string())
        log.info(f"[Validator] Email sent: {subject}")
    except Exception as e:
        log.warning(f"[Validator] Email send failed: {e}")


# ── MAIN VALIDATE FUNCTION ────────────────────────────────────────────────────

def validate_and_email(trade: dict, tweeted_score: int, stock: dict, history: dict, next_earnings: str = ""):
    """
    Send an immediate email notification after every post.
    Basic info only — no SEC lookup — so it fires fast and reliably.
    """
    ticker   = trade.get("ticker", "")
    insider  = trade.get("insider_name", "")
    title    = trade.get("insider_title", "")
    total    = trade.get("total_value", 0)
    shares   = trade.get("shares_traded", 0)
    price    = trade.get("price_per_share", 0)
    tx_date  = trade.get("transaction_date", "")
    code     = trade.get("transaction_code", "")
    after    = trade.get("shares_owned_after", 0)
    before   = trade.get("shares_owned_before", 0)
    high     = stock.get("52w_high", 0)
    cur_price = stock.get("price", 0)
    unusual  = history.get("unusual", False)
    consec   = history.get("consecutive_buys", 0)
    cluster  = history.get("cluster_count", 0)

    pct_high = round(((high - cur_price) / high) * 100, 1) if high and cur_price else 0
    pos_pct  = round(((shares / before) * 100), 1) if before >= 100 else None

    lines = [
        f"✅ POSTED — {code} ${ticker}",
        f"",
        f"Insider:    {insider}",
        f"Title:      {title}",
        f"Value:      ${total:,.0f} ({shares:,} shares @ ${price:.2f})",
        f"Trade date: {tx_date}",
        f"",
        f"Signal Score: {tweeted_score}/10",
        f"",
        f"CONTEXT:",
        f"  Stock price:    ${cur_price:.2f}",
        f"  52W High:       ${high:.2f} (−{pct_high}% from high)",
        f"  Position chg:   {'+' + str(pos_pct) + '%' if pos_pct else 'N/A (no prior shares)'}",
        f"  Shares after:   {after:,}",
        f"  Unusual:        {'Yes' if unusual else 'No'}",
        f"  Consecutive:    {consec} buys",
        f"  Cluster:        {cluster} insiders",
        f"",
        f"Validate: railway run python3 validate_tweet.py {ticker} \"{insider}\" --value {int(total)} --shares {shares} --price {price}",
    ]

    body    = "\n".join(lines)
    subject = f"🟢 Form4Wire — {code} ${ticker} — Score {tweeted_score}/10"
    _send_email(subject, body)
    log.info(f"[Validator] Email fired for ${ticker}")
