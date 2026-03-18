"""
sec_fetcher.py — Pulls and parses Form 4 filings from SEC EDGAR
"""

import re
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HEADERS = {
    "User-Agent": "Form4Wire support@form4wire.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,*/*",
}

# SEC EDGAR real-time RSS feed for Form 4 filings
# Returns the most recent 40 filings as they arrive — no pagination needed
# count=40 is the maximum SEC allows per request
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=40&search_text=&output=atom"

COMPANY_FACTS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def fetch_form4_feed() -> list[dict]:
    """
    Fetch latest Form 4 filings from SEC EDGAR real-time RSS feed.
    Returns up to 40 most recent filings — no pagination, no volume cap.
    seen_filings.json handles dedup so we never process the same filing twice.
    """
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[SEC] RSS feed fetch error: {e}")
        return []

    try:
        # Parse Atom XML feed
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
    except Exception as e:
        print(f"[SEC] RSS feed parse error: {e}")
        return []

    filings = []
    for entry in entries:
        try:
            # Extract filing ID from the <id> tag
            filing_id = entry.findtext("atom:id", "", ns).strip()

            # Extract title (contains company name and insider name)
            title = entry.findtext("atom:title", "", ns).strip()

            # Extract filing date
            filed_date = entry.findtext("atom:updated", "", ns).strip()
            if filed_date:
                filed_date = filed_date[:10]  # Keep just YYYY-MM-DD

            # Extract accession number and CIK from the filing URL
            link_el = entry.find("atom:link", ns)
            url = link_el.get("href", "") if link_el is not None else ""

            # Extract CIK and accession from URL
            # URL format: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=XXXXXX&type=4&...
            # or: https://www.sec.gov/Archives/edgar/data/CIK/accession-index.htm
            cik = ""
            accession = ""

            # Try to get the actual filing index URL from the summary
            summary = entry.findtext("atom:summary", "", ns)
            acc_match = re.search(r'(\d{10}-\d{2}-\d{6})', summary + url + filing_id)
            if acc_match:
                accession = acc_match.group(1)
                acc_clean = accession.replace("-", "")

            cik_match = re.search(r'CIK=(\d+)', url, re.IGNORECASE)
            if not cik_match:
                cik_match = re.search(r'/data/(\d+)/', url)
            if cik_match:
                cik = cik_match.group(1)

            # Build filing index URL
            if cik and accession:
                acc_clean = accession.replace("-", "")
                index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"
            else:
                index_url = url

            # Use filing_id as unique key (accession number if available, else atom id)
            unique_id = accession if accession else filing_id

            filings.append({
                "id":         unique_id,
                "title":      title,
                "updated":    filed_date,
                "filed_date": filed_date,
                "url":        index_url,
                "cik":        cik,
            })

        except Exception as e:
            print(f"[SEC] Error parsing RSS entry: {e}")
            continue

    print(f"[SEC] RSS feed: {len(filings)} filings fetched")
    return filings


def fetch_filing_xml(index_url: str) -> str:
    """Fetch the actual Form 4 XML from the filing index page."""
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        # Find all XML file links
        xml_matches = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)

        # Use the raw ownership XML, not the styled xslF345 version
        raw_xml_url = None
        for match in xml_matches:
            if "xsl" not in match.lower():
                raw_xml_url = "https://www.sec.gov" + match
                break

        if raw_xml_url:
            time.sleep(0.2)
            xml_resp = requests.get(raw_xml_url, headers=HEADERS, timeout=15)
            xml_resp.raise_for_status()
            return xml_resp.text

        return resp.text[:6000]

    except Exception as e:
        return f"Error fetching filing: {e}"


def fetch_company_data(cik: str) -> dict:
    """Fetch company metadata from SEC EDGAR."""
    if not cik:
        return {}
    try:
        url  = COMPANY_FACTS_URL.format(cik=int(cik))
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "name":     data.get("name", ""),
            "ticker":   data.get("tickers", [""])[0] if data.get("tickers") else "",
            "sic":      data.get("sic", ""),
            "sic_desc": data.get("sicDescription", ""),
            "state":    data.get("stateOfIncorporation", ""),
        }
    except Exception:
        return {}


def fetch_stock_price(ticker: str) -> dict:
    """Fetch current price, 52w high/low, market cap from Yahoo Finance."""
    if not ticker:
        return {}
    try:
        url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        meta   = data["chart"]["result"][0]["meta"]
        closes = data["chart"]["result"][0]["indicators"]["quote"][0].get("close", [])
        closes = [c for c in closes if c is not None]

        return {
            "price":      meta.get("regularMarketPrice", 0),
            "market_cap": meta.get("marketCap", 0),
            "52w_high":   max(closes) if closes else 0,
            "52w_low":    min(closes) if closes else 0,
            "currency":   meta.get("currency", "USD"),
        }
    except Exception:
        return {}


def fetch_short_interest(ticker: str) -> float:
    """Get short interest % from Yahoo Finance."""
    if not ticker:
        return 0.0
    try:
        url  = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=defaultKeyStatistics"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        data  = resp.json()
        stats = data["quoteSummary"]["result"][0]["defaultKeyStatistics"]
        return stats.get("shortPercentOfFloat", {}).get("raw", 0.0)
    except Exception:
        return 0.0


def fetch_next_earnings(ticker: str) -> str:
    """Get next earnings date from Yahoo Finance."""
    if not ticker:
        return ""
    try:
        url  = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        data   = resp.json()
        events = data["quoteSummary"]["result"][0]["calendarEvents"]
        dates  = events.get("earnings", {}).get("earningsDate", [])
        if dates:
            ts = dates[0].get("raw", 0)
            return datetime.utcfromtimestamp(ts).strftime("%b %d, %Y")
        return ""
    except Exception:
        return ""


def parse_transactions_from_xml(xml: str) -> dict:
    """
    Parse transaction rows directly from Form 4 XML.
    Returns aggregated shares data — more reliable than asking Claude.
    """
    import xml.etree.ElementTree as ET
    result = {}
    try:
        root = ET.fromstring(xml)

        # Collect all non-derivative transactions with ownership bucket info
        # Real SEC XML: transactionCode is direct text under transactionCoding/transactionCode
        # All other values are wrapped in <value> child tags
        transactions = []
        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el     = txn.find(".//transactionCoding/transactionCode")
            shares_el   = txn.find(".//transactionShares/value")
            price_el    = txn.find(".//transactionPricePerShare/value")
            owned_el    = txn.find(".//sharesOwnedFollowingTransaction/value")
            date_el     = txn.find(".//transactionDate/value")
            form_el     = txn.find(".//directOrIndirectOwnership/value")
            nature_el   = txn.find(".//natureOfOwnership/value")

            if code_el is None or shares_el is None:
                continue

            code = code_el.text.strip() if code_el.text else ""
            try:
                shares      = float(shares_el.text.strip())
                price       = float(price_el.text.strip()) if price_el is not None and price_el.text else 0.0
                owned_after = float(owned_el.text.strip()) if owned_el is not None and owned_el.text else 0.0
                date        = date_el.text.strip() if date_el is not None and date_el.text else ""
                form        = form_el.text.strip() if form_el is not None and form_el.text else "D"
                nature      = nature_el.text.strip() if nature_el is not None and nature_el.text else ""
            except (ValueError, AttributeError):
                continue

            # Bucket key = ownership form + nature (e.g. "D|", "I|By Trust", "I|By Managed Account")
            bucket = f"{form}|{nature}"

            transactions.append({
                "code":        code,
                "shares":      shares,
                "price":       price,
                "owned_after": owned_after,
                "date":        date,
                "bucket":      bucket,
            })

        if not transactions:
            return result

        # Find dominant transaction code (P for buy, S for sell)
        from collections import Counter
        codes = Counter(t["code"] for t in transactions)
        dominant_code = codes.most_common(1)[0][0]

        # Filter to dominant code rows only
        rows = [t for t in transactions if t["code"] == dominant_code]
        if not rows:
            return result

        # Total shares traded = sum across ALL rows (all buckets)
        total_shares = sum(r["shares"] for r in rows)
        total_value  = sum(r["shares"] * r["price"] for r in rows)
        avg_price    = total_value / total_shares if total_shares else 0
        first_date   = rows[0]["date"]
        last_date    = rows[-1]["date"]

        # For shares_owned_after: sum the LAST row of each ownership bucket
        # Handles Direct + Trust + Managed Account etc. correctly
        bucket_last = {}
        for r in rows:
            bucket_last[r["bucket"]] = r["owned_after"]  # last row per bucket wins

        total_owned_after = sum(bucket_last.values())
        owned_before = (total_owned_after - total_shares if dominant_code == "P"
                        else total_owned_after + total_shares)

        result = {
            "transaction_code":    dominant_code,
            "shares_traded":       int(total_shares),
            "price_per_share":     round(avg_price, 4),
            "total_value":         round(total_value, 2),
            "transaction_date":    first_date,
            "transaction_date_end": last_date if last_date != first_date else "",
            "shares_owned_after":  int(total_owned_after),
            "shares_owned_before": int(owned_before),
        }

    except Exception as e:
        pass  # Fall back to Claude's values if XML parsing fails

    return result
